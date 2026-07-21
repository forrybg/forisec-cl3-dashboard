"""
context/index_builder.py

PHASE 2 -- Project-scoped SQLite context index.

Builds a completely new, project-scoped SQLite database (context.db)
containing FTS5-indexed chunks of ONLY the canonical documents listed
in config/canonical_documents.json (the fixed source allowlist for
this project). Every generation is a full, clean rebuild -- for a
corpus this small, a full atomic rebuild is simpler and strictly safer
than incremental diffing: it guarantees a deleted or renamed source
can never leave an orphaned chunk behind, with no separate cleanup
pass required.

HARD BOUNDARIES (FINAL PHASE 2 task spec):
  - Never reads the old foritech-os runtime/memory/memory.db.
  - Never reads the old foritech-os/server/search/index.db.
  - Never reads or uses the old foritech-horizon corpus.
  - Never calls the old Search API (:8103) for indexed documents --
    this builder makes no HTTP calls at all except the one, optional,
    stateless embeddings call described below.
  - Never copies old records/chunks; never auto-migrates old memories.
  - The source allowlist is only ever the documents listed in
    config/canonical_documents.json, resolved through
    agents.common.safe_repo_path (rejects absolute paths, `../`
    escapes, and symlink escapes), and restricted to .md/.txt text
    files only. No directory scan is ever performed.

SEMANTIC EMBEDDINGS (optional, best-effort):
  If FORISEC_EMBEDDINGS_URL (default http://127.0.0.1:8101/embed) is
  reachable at build time, this builder calls it as a stateless model
  worker (POST {"texts": [...], "normalize": true}) and stores the
  returned vectors in this NEW database's own chunks.embedding column.
  It never reads the old index.db, never writes to it, and if the
  worker is unreachable, every chunk's embedding stays NULL -- lexical
  (FTS5) retrieval remains fully functional; semantic unavailability is
  recorded honestly in meta.index_model_version, never silently hidden.

ATOMICITY: builds into context.db.tmp-<generation_id> next to the
final context.db, fully populates and validates it, then calls
os.replace() to publish it. On any failure, the temp file is removed
and the previous valid context.db (if any) is left completely
untouched -- callers must treat a raised exception here as "keep the
old generation, do not publish".

Usage: python -m context.index_builder
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents.common import safe_repo_path, UnsafeRepositoryPathError, get_repo_commit
from agents.docs_controller import STATUS_MARKER_RE, load_manifest

DB_FILENAME = "context.db"
SCHEMA_VERSION = "1.0"
INDEX_MODEL_VERSION_LEXICAL = "lexical-fts5-v1"
INDEX_MODEL_VERSION_SEMANTIC = "lexical-fts5-v1+embeddings-bge-m3-v1"

ALLOWED_SOURCE_SUFFIXES = {".md", ".txt"}

MAX_CHUNK_TOKENS = 450
MIN_CHUNK_TOKENS = 300
OVERLAP_TOKENS = 40
CHARS_PER_TOKEN = 4  # same heuristic as pipeline/context_builder.py

EMBEDDINGS_URL = os.environ.get("FORISEC_EMBEDDINGS_URL", "http://127.0.0.1:8101/embed")
EMBEDDINGS_DIM = 1024  # BAAI/bge-m3
EMBEDDINGS_TIMEOUT = 10
EMBEDDINGS_BATCH_SIZE = 16

# Defense-in-depth denylist, even though sources only ever come from the
# manifest allowlist (belt-and-suspenders against a manifest entry that
# should never have pointed at one of these in the first place).
DENIED_PATH_SUBSTRINGS = (
    ".env", "secret", "credential", "id_rsa", "id_ed25519", ".git/",
    ".ssh/", ".aws/", ".gnupg/",
)


class IndexBuildError(RuntimeError):
    pass


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "section"


def _token_estimate(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _fetch_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Best-effort, stateless call to the embeddings worker. Returns
    None (never raises) if the worker is unreachable or responds
    unexpectedly -- callers must treat None as 'semantic unavailable',
    never as an error that should abort the (lexical) build."""
    try:
        payload = json.dumps({"texts": texts, "normalize": True}).encode("utf-8")
        req = urllib.request.Request(
            EMBEDDINGS_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=EMBEDDINGS_TIMEOUT) as r:
            resp = json.loads(r.read())
        vectors = resp.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            return None
        return vectors
    except Exception:
        return None


def _is_denied_path(rel_path: str) -> bool:
    lowered = rel_path.lower()
    return any(bad in lowered for bad in DENIED_PATH_SUBSTRINGS)


def _extract_canonical_status(text: str) -> str | None:
    m = STATUS_MARKER_RE.search(text)
    return m.group(1) if m else None


def _split_oversized(heading, section_key, text, max_chars, overlap_chars):
    pieces = []
    pos = 0
    while pos < len(text):
        piece = text[pos:pos + max_chars]
        if pos + max_chars < len(text):
            last_space = piece.rfind(" ")
            if last_space > overlap_chars:
                piece = piece[:last_space]
        piece = piece.strip()
        if piece:
            pieces.append((heading, section_key, piece))
        advance = max(len(piece) - overlap_chars, 1)
        pos += advance
    return pieces


def _chunk_document(text: str) -> list[tuple[str | None, str, int, str]]:
    """Section-aware Markdown chunking targeting MIN_CHUNK_TOKENS -
    MAX_CHUNK_TOKENS per chunk. Headings are section boundaries, but
    consecutive small sections (a document typically has many short
    H2-H6 sections) are MERGED into a single chunk until the target
    size is reached, rather than emitting one tiny chunk per heading.
    A single section that on its own exceeds MAX_CHUNK_TOKENS is split
    into consecutive, slightly overlapping pieces. Returns a list of
    (heading, section_key, chunk_index_within_source, chunk_text)."""
    heading_re = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
    matches = list(heading_re.finditer(text))

    sections: list[tuple[str | None, str]] = []
    if not matches:
        if text.strip():
            sections.append((None, text.strip()))
    else:
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                sections.append((None, preamble))
        for i, m in enumerate(matches):
            heading = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = f"{m.group(0)}\n{text[start:end]}".strip()
            if body:
                sections.append((heading, body))

    max_chars = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
    min_chars = MIN_CHUNK_TOKENS * CHARS_PER_TOKEN
    overlap_chars = OVERLAP_TOKENS * CHARS_PER_TOKEN

    raw_chunks: list[tuple[str | None, str, str]] = []  # (heading, section_key, text)
    buffer_heading = None
    buffer_section_key = None
    buffer_text = ""

    def flush():
        nonlocal buffer_text, buffer_heading, buffer_section_key
        if buffer_text.strip():
            raw_chunks.append((buffer_heading, buffer_section_key, buffer_text.strip()))
        buffer_text = ""
        buffer_heading = None
        buffer_section_key = None

    for heading, body in sections:
        section_key = _slugify(heading) if heading else "preamble"

        if len(body) > max_chars:
            flush()
            raw_chunks.extend(_split_oversized(heading, section_key, body, max_chars, overlap_chars))
            continue

        if not buffer_text:
            buffer_heading, buffer_section_key, buffer_text = heading, section_key, body
        else:
            merged = buffer_text + "\n\n" + body
            if len(merged) <= max_chars:
                buffer_text = merged
            else:
                flush()
                buffer_heading, buffer_section_key, buffer_text = heading, section_key, body

        if len(buffer_text) >= min_chars:
            flush()

    flush()

    return [(h, sk, i, t) for i, (h, sk, t) in enumerate(raw_chunks)]


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE meta (
            schema_version TEXT NOT NULL,
            index_model_version TEXT NOT NULL,
            project_id TEXT NOT NULL,
            proposal_repo_commit TEXT,
            service_commit TEXT,
            generated_at TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL
        );

        CREATE TABLE sources (
            source_path TEXT PRIMARY KEY,
            source_hash TEXT NOT NULL,
            source_type TEXT NOT NULL,
            canonical_status TEXT,
            repo_commit TEXT,
            indexed_at TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            superseded INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE chunks (
            chunk_id TEXT UNIQUE NOT NULL,
            source_path TEXT NOT NULL,
            heading TEXT,
            section_key TEXT,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            repo_commit TEXT,
            embedding BLOB,
            FOREIGN KEY (source_path) REFERENCES sources(source_path)
        );

        CREATE INDEX idx_chunks_source_path ON chunks(source_path);

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            text, heading,
            content='chunks', content_rowid='rowid'
        );
    """)


def build(repo_root: Path, state_dir: Path, service_repo_root: Path | None = None,
          use_semantic: bool = True) -> dict:
    """
    Builds a fresh generation of context.db atomically. Returns a
    summary dict (generation_id, source_count, chunk_count,
    semantic_available, db_path, repo_commit, service_commit).
    Raises IndexBuildError on any failure that must NOT publish a
    partial database -- the caller (context_index_builder CLI /
    refresh_agents.sh) must let this propagate as a non-zero exit.
    """
    if service_repo_root is None:
        service_repo_root = Path(__file__).resolve().parents[1]

    proposal_repo_commit = get_repo_commit(repo_root)
    service_commit = get_repo_commit(service_repo_root)
    generation_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        manifest = load_manifest(repo_root)
    except Exception as e:
        raise IndexBuildError(f"Cannot load config/canonical_documents.json: {e}") from e

    project_id = manifest.get("project") or "UNKNOWN"
    documents = manifest.get("documents", [])

    state_dir.mkdir(parents=True, exist_ok=True)
    final_db_path = state_dir / DB_FILENAME
    tmp_db_path = state_dir / f"{DB_FILENAME}.tmp-{generation_id}"

    if tmp_db_path.exists():
        tmp_db_path.unlink()

    conn = sqlite3.connect(str(tmp_db_path))
    try:
        _create_schema(conn)

        source_rows = []
        chunk_rows = []  # (heading, section_key, chunk_index, text, source_path)

        for doc in documents:
            rel_path = doc.get("path")
            if not rel_path:
                continue
            if _is_denied_path(rel_path):
                continue
            suffix = Path(rel_path).suffix.lower()
            if suffix not in ALLOWED_SOURCE_SUFFIXES:
                continue

            try:
                resolved = safe_repo_path(repo_root, rel_path)
            except UnsafeRepositoryPathError:
                continue  # manifest entry would escape the repo -- never indexed

            if not resolved.exists() or not resolved.is_file():
                continue  # listed but missing -- simply absent from this generation

            try:
                raw_bytes = resolved.read_bytes()
                text = raw_bytes.decode("utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable -- never indexed

            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            canonical_status = _extract_canonical_status(text)
            superseded = 1 if (canonical_status or "").upper() == "SUPERSEDED" else 0

            source_rows.append((
                rel_path, source_hash, suffix.lstrip("."), canonical_status,
                proposal_repo_commit, generated_at, _token_estimate(text), superseded,
            ))

            for heading, section_key, chunk_index, chunk_text in _chunk_document(text):
                chunk_rows.append((heading, section_key, chunk_index, chunk_text, rel_path))

        conn.executemany(
            "INSERT INTO sources (source_path, source_hash, source_type, canonical_status, "
            "repo_commit, indexed_at, token_estimate, superseded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            source_rows,
        )

        semantic_available = False
        embeddings_by_index: dict[int, list[float]] = {}
        if use_semantic and chunk_rows:
            texts = [c[3] for c in chunk_rows]
            for batch_start in range(0, len(texts), EMBEDDINGS_BATCH_SIZE):
                batch = texts[batch_start:batch_start + EMBEDDINGS_BATCH_SIZE]
                vectors = _fetch_embeddings(batch)
                if vectors is None:
                    embeddings_by_index = {}
                    semantic_available = False
                    break
                for i, vec in enumerate(vectors):
                    embeddings_by_index[batch_start + i] = vec
                semantic_available = True

        cur = conn.cursor()
        for i, (heading, section_key, chunk_index, chunk_text, source_path) in enumerate(chunk_rows):
            chunk_id = f"{source_path}#{chunk_index}"
            text_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            embedding_blob = None
            if i in embeddings_by_index:
                embedding_blob = _pack_embedding(embeddings_by_index[i])
            cur.execute(
                "INSERT INTO chunks (chunk_id, source_path, heading, section_key, chunk_index, "
                "text, text_hash, token_estimate, repo_commit, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk_id, source_path, heading, section_key, chunk_index, chunk_text,
                 text_hash, _token_estimate(chunk_text), proposal_repo_commit, embedding_blob),
            )
            rowid = cur.lastrowid
            cur.execute(
                "INSERT INTO chunks_fts (rowid, text, heading) VALUES (?, ?, ?)",
                (rowid, chunk_text, heading or ""),
            )

        index_model_version = INDEX_MODEL_VERSION_SEMANTIC if semantic_available else INDEX_MODEL_VERSION_LEXICAL

        conn.execute(
            "INSERT INTO meta (schema_version, index_model_version, project_id, "
            "proposal_repo_commit, service_commit, generated_at, generation_id, "
            "source_count, chunk_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (SCHEMA_VERSION, index_model_version, project_id, proposal_repo_commit,
             service_commit, generated_at, generation_id, len(source_rows), len(chunk_rows)),
        )

        conn.commit()

        # Validate before publishing: required tables + non-empty meta row.
        row = conn.execute("SELECT source_count, chunk_count FROM meta").fetchone()
        if row is None:
            raise IndexBuildError("meta table is empty after build -- refusing to publish.")
        if row[0] != len(source_rows) or row[1] != len(chunk_rows):
            raise IndexBuildError("meta counts do not match built rows -- refusing to publish.")
    except Exception:
        conn.close()
        if tmp_db_path.exists():
            tmp_db_path.unlink()
        raise
    else:
        conn.close()

    os.replace(str(tmp_db_path), str(final_db_path))

    return {
        "generation_id": generation_id,
        "source_count": len(source_rows),
        "chunk_count": len(chunk_rows),
        "semantic_available": semantic_available,
        "index_model_version": index_model_version,
        "db_path_name": DB_FILENAME,
        "repo_commit": proposal_repo_commit,
        "service_commit": service_commit,
        "generated_at": generated_at,
    }


def main():
    repo_root = Path(os.environ.get("FORISEC_REPO_ROOT", "")).expanduser()
    state_dir = Path(os.environ.get("FORISEC_STATE_DIR", "")).expanduser()
    if not repo_root or not state_dir:
        print("[context_index_builder] CONFIG ERROR: FORISEC_REPO_ROOT and FORISEC_STATE_DIR must be set.")
        raise SystemExit(1)
    context_dir = state_dir / "context"
    try:
        result = build(repo_root, context_dir)
    except IndexBuildError as e:
        print(f"[context_index_builder] BUILD FAILED (old generation, if any, left untouched): {e}")
        raise SystemExit(1)
    print(f"[context_index_builder] completed -- generation={result['generation_id']} "
          f"sources={result['source_count']} chunks={result['chunk_count']} "
          f"semantic={result['semantic_available']} -- db written to {context_dir}")


if __name__ == "__main__":
    main()
