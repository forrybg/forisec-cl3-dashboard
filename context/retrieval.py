"""
context/retrieval.py

PHASE 2 -- read-only retrieval over the project-scoped context.db.

Implements the LEVEL 2 (section) and LEVEL 3 (search/source) context
levels described in the FINAL PHASE 2 task spec. Every function here:
  - only ever opens context.db in SQLite read-only URI mode
    (`file:...?mode=ro`) -- this module can never write to the database;
  - only ever queries via parameterized SQL (no string-built SQL, no
    f-string interpolation of user input into a query);
  - never builds or rebuilds the index (that is context/index_builder.py,
    run only via scripts/refresh_agents.sh);
  - never reads the old foritech-os memory.db or search/index.db;
  - never exposes an absolute filesystem path in a response.

LEVEL 1 (bootstrap) is pipeline/context_builder.py's
project_context_state.json, read via app.state_reader.read_context_bootstrap
-- this module does not duplicate that logic, it composes with it for
LEVEL 2's structured-summary half.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

from context.index_builder import (
    DB_FILENAME, EMBEDDINGS_DIM, _fetch_embeddings, unpack_embedding,
)

SECTION_ALLOWLIST = {
    "architecture": "architecture_summary",
    "current_state": "current_state",
    "work_packages": "work_package_summary",
    "partners": "partner_summary",
    "budget": "budget_summary",
    "evidence": "evidence_summary",
    "evaluation": "evaluation_summary",
    "decisions": None,  # composed from open/recent/superseded
    "completed_work": None,  # composed from aggregate/proposal/service
    "constraints": "constraints",
    "forbidden_changes": "forbidden_changes",
    "next_actions": "next_actions",
}

QUERY_MIN_CHARS = 2
QUERY_MAX_CHARS = 300
TOP_K_DEFAULT = 5
TOP_K_MAX = 10

SECTION_TOKEN_BUDGET = 2500
SEARCH_TOKEN_BUDGET = 3000
SOURCE_TOKEN_BUDGET = 3000
SNIPPET_TOKEN_BUDGET = 500
REPO_MAP_TOKEN_BUDGET = 2500

CHARS_PER_TOKEN = 4

# A section value is rejected outright if it looks anything like a
# filesystem path -- LEVEL 2 never accepts a path as a "section".
_PATH_LIKE_RE = re.compile(r"[\\/]|\.\.|^\.|\.md$|\.txt$|\.json$")


class RetrievalError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _estimate_tokens(obj) -> int:
    return max(0, round(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN))


def _live_repo_commit(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def _context_db_path(state_dir: Path) -> Path:
    return state_dir / "context" / DB_FILENAME


def _open_db_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _read_db_meta(state_dir: Path) -> dict | None:
    db_path = _context_db_path(state_dir)
    if not db_path.exists():
        return None
    try:
        conn = _open_db_readonly(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT schema_version, index_model_version, project_id, proposal_repo_commit, "
            "service_commit, generated_at, generation_id, source_count, chunk_count FROM meta"
        ).fetchone()
    except sqlite3.DatabaseError:
        conn.close()
        return None
    conn.close()
    if row is None:
        return None
    keys = ["schema_version", "index_model_version", "project_id", "proposal_repo_commit",
            "service_commit", "generated_at", "generation_id", "source_count", "chunk_count"]
    return dict(zip(keys, row))


def _envelope(state_dir: Path, repo_root: Path, extra: dict) -> dict:
    """Common envelope fields required on every LEVEL 2/3 response."""
    meta = _read_db_meta(state_dir)
    live_commit = _live_repo_commit(repo_root)

    if meta is None:
        base = {
            "available": False, "freshness": "UNAVAILABLE",
            "repo_commit": None, "service_commit": None, "generation_id": None,
            "sources": [], "reason": "context.db is not available.",
        }
    else:
        freshness = "FRESH"
        if not live_commit or live_commit != meta["proposal_repo_commit"]:
            freshness = "STALE"
        base = {
            "available": True, "freshness": freshness,
            "repo_commit": meta["proposal_repo_commit"], "service_commit": meta["service_commit"],
            "generation_id": meta["generation_id"], "sources": [],
        }
    base.update(extra)
    base["token_estimate"] = _estimate_tokens(base)
    return base


# ── LEVEL 2: section ──────────────────────────────────────────────────

def get_section(state_dir: Path, repo_root: Path, section: str, bootstrap: dict) -> dict:
    if section not in SECTION_ALLOWLIST:
        raise RetrievalError("UNKNOWN_SECTION", f"'{section}' is not in the section allowlist.")
    if _PATH_LIKE_RE.search(section):
        raise RetrievalError("INVALID_SECTION", "section must be a bare allowlist name, not a path.")

    if not bootstrap.get("available"):
        return _envelope(state_dir, repo_root, {"section": section, "summary": None})

    if section == "decisions":
        summary = {
            "open": bootstrap.get("open_decisions", []),
            "recent": bootstrap.get("recent_decisions", []),
            "superseded": bootstrap.get("superseded_decisions", []),
        }
    elif section == "completed_work":
        summary = {
            "aggregate": bootstrap.get("completed_work", []),
            "proposal": bootstrap.get("proposal_completed_work", []),
            "service": bootstrap.get("service_completed_work", []),
        }
    else:
        summary = bootstrap.get(SECTION_ALLOWLIST[section])

    source_paths = bootstrap.get("source_map", {}).get(
        "work_package_summary" if section == "work_packages" else
        "architecture_summary" if section == "architecture" else
        "recent_decisions" if section == "decisions" else
        SECTION_ALLOWLIST[section] or "", []
    )

    snippets = []
    db_path = _context_db_path(state_dir)
    if db_path.exists() and source_paths:
        try:
            conn = _open_db_readonly(db_path)
            try:
                for sp in source_paths[:6]:
                    rows = conn.execute(
                        "SELECT source_path, heading, text FROM chunks "
                        "WHERE source_path = ? ORDER BY chunk_index LIMIT 1",
                        (sp,),
                    ).fetchall()
                    for source_path, heading, text in rows:
                        snippet_text = text[:SNIPPET_TOKEN_BUDGET * CHARS_PER_TOKEN]
                        snippets.append({"source_path": source_path, "heading": heading, "text": snippet_text})
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            snippets = []

    result = _envelope(state_dir, repo_root, {
        "section": section, "summary": summary, "snippets": snippets,
        "sources": [{"source_path": sp} for sp in source_paths],
    })

    # Enforce the LEVEL 2 token ceiling by dropping snippets first, then
    # trimming the summary's own size is out of scope (summary already
    # comes from the size-budgeted bootstrap) -- snippets are the only
    # thing this layer adds, so they are what gets cut if over budget.
    while result["token_estimate"] > SECTION_TOKEN_BUDGET and result.get("snippets"):
        result["snippets"].pop()
        result["token_estimate"] = _estimate_tokens(result)
    return result


# ── LEVEL 3: search ───────────────────────────────────────────────────

def _fts_escape(query: str) -> str:
    """FTS5 query syntax has special characters (", *, -, etc). To keep
    this a safe, OR-of-terms search and never let user input reach
    FTS5's own query-language operators, each whitespace-separated term
    is individually wrapped as its own quoted phrase (internal quotes
    doubled, SQLite FTS5's own escaping rule for a quoted-string token),
    then joined with OR. A lone term becomes a single quoted phrase --
    functionally a safe substring/word search, never letting a raw `*`,
    `-`, or unescaped `"` reach FTS5's operator grammar."""
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return '""'
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(quoted)


def search(state_dir: Path, repo_root: Path, query: str, top_k: int = TOP_K_DEFAULT,
           section: str | None = None) -> dict:
    if not isinstance(query, str) or not (QUERY_MIN_CHARS <= len(query) <= QUERY_MAX_CHARS):
        raise RetrievalError(
            "INVALID_QUERY",
            f"query must be a string between {QUERY_MIN_CHARS} and {QUERY_MAX_CHARS} characters.",
        )
    top_k = max(1, min(top_k or TOP_K_DEFAULT, TOP_K_MAX))

    section_source_paths = None
    if section is not None:
        if section not in SECTION_ALLOWLIST:
            raise RetrievalError("UNKNOWN_SECTION", f"'{section}' is not in the section allowlist.")

    db_path = _context_db_path(state_dir)
    if not db_path.exists():
        return _envelope(state_dir, repo_root, {"query": query, "results": [], "semantic_used": False})

    try:
        conn = _open_db_readonly(db_path)
    except sqlite3.OperationalError:
        return _envelope(state_dir, repo_root, {"query": query, "results": [], "semantic_used": False})

    try:
        try:
            fts_query = _fts_escape(query)
            rows = conn.execute(
                "SELECT chunks.source_path, chunks.heading, chunks.text, chunks.embedding, "
                "bm25(chunks_fts) AS rank "
                "FROM chunks_fts JOIN chunks ON chunks.rowid = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, max(top_k * 3, top_k)),
            ).fetchall()

            semantic_used = False
            query_vec = None
            meta = _read_db_meta(state_dir)
            if meta and "embeddings" in (meta.get("index_model_version") or ""):
                vectors = _fetch_embeddings([query])
                if vectors:
                    query_vec = vectors[0]

            scored = []
            for source_path, heading, text, embedding_blob, rank in rows:
                lexical_score = -float(rank)  # bm25: lower is better -> invert for "higher is better"
                semantic_score = None
                if query_vec is not None and embedding_blob is not None:
                    try:
                        chunk_vec = unpack_embedding(embedding_blob)
                        semantic_score = _cosine_similarity(query_vec, chunk_vec)
                        semantic_used = True
                    except Exception:
                        semantic_score = None
                scored.append({
                    "source_path": source_path, "heading": heading,
                    "snippet": text[:SNIPPET_TOKEN_BUDGET * CHARS_PER_TOKEN],
                    "lexical_score": round(lexical_score, 4),
                    "semantic_score": round(semantic_score, 4) if semantic_score is not None else None,
                })

            if semantic_used:
                scored.sort(key=lambda r: (r["semantic_score"] if r["semantic_score"] is not None else -1e9), reverse=True)
            scored = scored[:top_k]
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # Corrupt / partially-created / non-SQLite context.db discovered only
        # once a real query runs (SQLite's own connect() is lazy) -- degrade
        # to "no results" rather than propagate a 500 to the caller.
        return _envelope(state_dir, repo_root, {"query": query, "results": [], "semantic_used": False})

    result = _envelope(state_dir, repo_root, {
        "query": query, "results": scored, "semantic_used": semantic_used,
        "sources": [{"source_path": r["source_path"], "heading": r["heading"]} for r in scored],
    })
    while result["token_estimate"] > SEARCH_TOKEN_BUDGET and result.get("results"):
        result["results"].pop()
        result["sources"] = result["sources"][:len(result["results"])]
        result["token_estimate"] = _estimate_tokens(result)
    return result


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── LEVEL 3: source ───────────────────────────────────────────────────

def get_source(state_dir: Path, repo_root: Path, path: str) -> dict:
    if not isinstance(path, str) or not path:
        raise RetrievalError("INVALID_PATH", "path must be a non-empty string.")
    if path.startswith("/") or path.startswith("~"):
        raise RetrievalError("ABSOLUTE_PATH_REJECTED", "Absolute paths are never accepted.")
    if ".." in path.split("/"):
        raise RetrievalError("PATH_TRAVERSAL_REJECTED", "Path traversal segments are never accepted.")

    db_path = _context_db_path(state_dir)
    if not db_path.exists():
        return _envelope(state_dir, repo_root, {"path": path, "chunks": [], "truncated": False})

    try:
        conn = _open_db_readonly(db_path)
    except sqlite3.OperationalError:
        return _envelope(state_dir, repo_root, {"path": path, "chunks": [], "truncated": False})

    try:
        try:
            source_row = conn.execute(
                "SELECT source_path, source_hash, source_type, canonical_status, repo_commit, "
                "token_estimate, superseded FROM sources WHERE source_path = ?",
                (path,),
            ).fetchone()
            if source_row is None:
                raise RetrievalError(
                    "PATH_NOT_IN_ALLOWLIST",
                    "This path is not a canonical source in the current context index generation.",
                )

            chunk_rows = conn.execute(
                "SELECT heading, section_key, chunk_index, text FROM chunks "
                "WHERE source_path = ? ORDER BY chunk_index",
                (path,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # Corrupt / partially-created / non-SQLite context.db discovered only
        # once a real query runs -- degrade to "unavailable" rather than
        # propagate a 500 to the caller.
        return _envelope(state_dir, repo_root, {"path": path, "chunks": [], "truncated": False})

    keys = ["source_path", "source_hash", "source_type", "canonical_status", "repo_commit",
            "token_estimate", "superseded"]
    source_info = dict(zip(keys, source_row))
    source_info["superseded"] = bool(source_info["superseded"])

    chunks = []
    running_tokens = 0
    truncated = False
    # Headroom for envelope/source_info overhead, plus a fixed per-chunk
    # allowance for this chunk's own JSON keys/punctuation (heading,
    # section_key, chunk_index, quoting, commas) -- not just the chunk
    # text length -- so a document made of many small chunks can't push
    # the real serialized response past SOURCE_TOKEN_BUDGET.
    budget = SOURCE_TOKEN_BUDGET - 200
    PER_CHUNK_OVERHEAD_TOKENS = 20
    for heading, section_key, chunk_index, text in chunk_rows:
        piece_tokens = max(1, round(len(text) / CHARS_PER_TOKEN)) + PER_CHUNK_OVERHEAD_TOKENS
        if running_tokens + piece_tokens > budget:
            truncated = True
            break
        chunks.append({"heading": heading, "section_key": section_key, "chunk_index": chunk_index, "text": text})
        running_tokens += piece_tokens

    result = _envelope(state_dir, repo_root, {
        "path": path, "source": source_info, "chunks": chunks, "truncated": truncated,
        "sources": [{"source_path": path, "source_hash": source_info["source_hash"]}],
    })
    # The pre-estimate loop above is a fast first pass, but real JSON
    # serialization overhead (key names, punctuation, unicode escaping)
    # can still push the true estimate over budget for some documents --
    # measure the actual serialized envelope and keep trimming the
    # least-recently-added chunk until it is verifiably within budget,
    # exactly like get_section()/search() already do.
    while result["token_estimate"] > SOURCE_TOKEN_BUDGET and result["chunks"]:
        result["chunks"].pop()
        result["truncated"] = True
        result["token_estimate"] = _estimate_tokens(result)
    return result


# ── repo map (this SERVICE's own codebase, never the proposal repo) ────

def get_repo_map(state_dir: Path, repo_root: Path) -> dict:
    """Deterministic catalog of forisec-cl3-dashboard's own source tree
    (path, kind, summary, top-level functions/classes, line count) --
    built once per context.db generation by context/index_builder.py's
    _scan_repo_map(). Exists so a brand-new chat can see "what file does
    what" in one query instead of grepping the whole tree."""
    db_path = _context_db_path(state_dir)
    if not db_path.exists():
        return _envelope(state_dir, repo_root, {"files": []})

    try:
        conn = _open_db_readonly(db_path)
    except sqlite3.OperationalError:
        return _envelope(state_dir, repo_root, {"files": []})

    try:
        try:
            rows = conn.execute(
                "SELECT path, kind, summary, top_level_functions, top_level_classes, "
                "line_count FROM repo_map ORDER BY path"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return _envelope(state_dir, repo_root, {"files": []})

    files = []
    for path, kind, summary, functions_json, classes_json, line_count in rows:
        try:
            functions = json.loads(functions_json) if functions_json else []
        except (TypeError, json.JSONDecodeError):
            functions = []
        try:
            classes = json.loads(classes_json) if classes_json else []
        except (TypeError, json.JSONDecodeError):
            classes = []
        files.append({
            "path": path, "kind": kind, "summary": summary,
            "top_level_functions": functions, "top_level_classes": classes,
            "line_count": line_count,
        })

    result = _envelope(state_dir, repo_root, {"files": files})
    while result["token_estimate"] > REPO_MAP_TOKEN_BUDGET and result["files"]:
        result["files"].pop()
        result["token_estimate"] = _estimate_tokens(result)
    return result
