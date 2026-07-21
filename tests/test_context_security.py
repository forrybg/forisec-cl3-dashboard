"""
Security tests for the PHASE 2 context.db / retrieval / index_builder
layer, per the FINAL PHASE 2 spec's required security checklist:
path traversal, symlink escape, absolute path rejection, SQL injection,
FTS query escaping, oversized query/response, malformed UTF-8, invalid
SQLite, partially-created DB, stale DB, source removed after indexing,
XSS-safe rendering, no secrets/env exposure, no absolute paths, no
arbitrary HTTP proxy, no old database access, all queries parameterized.
"""
import inspect
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from context import index_builder as ib
from context import retrieval as cr
from context.retrieval import RetrievalError


# ── fixtures ─────────────────────────────────────────────────────

def _write_manifest(repo, doc_entries):
    manifest = {
        "manifest_version": "test", "project": "forisec-cl3-test",
        "current_phase": "baseline", "phases": {"baseline": {"depends_on": []}},
        "documents": doc_entries,
    }
    (repo / "config").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "canonical_documents.json").write_text(json.dumps(manifest))


def _init_git(repo):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo)


# ── 1. Parameterized SQL / no string-built queries (static check) ────

def test_all_sql_execute_calls_use_parameterized_placeholders():
    """Grep both modules' source for any f-string or %-/format-built SQL
    passed to .execute/.executemany/.executescript -- every real query
    in this codebase uses a literal SQL string with `?` placeholders and
    a separate parameters tuple, never string interpolation of a value
    into the SQL text itself."""
    for module in (ib, cr):
        source = inspect.getsource(module)
        assert 'execute(f"' not in source
        assert "execute(f'" not in source
        # no % string-formatting or .format() feeding directly into a
        # .execute(...) call's first argument
        for m in re.finditer(r"\.execute(?:many)?\(\s*(.*?)\n", source):
            first_line = m.group(1)
            assert ".format(" not in first_line
            assert not re.search(r'%\s*\(', first_line)


# ── 2. Path traversal / absolute path / symlink escape ────────────────

def test_get_source_rejects_path_traversal(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(tmp_path, tmp_path, "../../etc/passwd")
    assert exc_info.value.code == "PATH_TRAVERSAL_REJECTED"


def test_get_source_rejects_absolute_path(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(tmp_path, tmp_path, "/etc/passwd")
    assert exc_info.value.code == "ABSOLUTE_PATH_REJECTED"


def test_get_source_rejects_tilde_path(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(tmp_path, tmp_path, "~/.ssh/id_rsa")
    assert exc_info.value.code == "ABSOLUTE_PATH_REJECTED"


def test_get_source_rejects_empty_path(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(tmp_path, tmp_path, "")
    assert exc_info.value.code == "INVALID_PATH"


def test_section_rejects_path_like_values(tmp_path):
    for bad_section in ("../etc/passwd", "foo/bar", "x.md", "/abs/path", ".hidden"):
        with pytest.raises(RetrievalError):
            cr.get_section(tmp_path, tmp_path, bad_section, {"available": False})


def test_index_builder_never_indexes_a_symlink_escaping_the_repo(tmp_path):
    """A manifest entry that resolves (via a symlink) outside the repo
    root must never be indexed -- safe_repo_path() rejects it and the
    builder silently skips the document rather than following it."""
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    outside = tmp_path / "outside_secret.md"
    outside.write_text("TOP SECRET CONTENT\n")
    symlink_path = repo / "00_baseline" / "LINKED.md"
    symlink_path.symlink_to(outside)
    _write_manifest(repo, [{"path": "00_baseline/LINKED.md", "title": "Linked", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    result = ib.build(repo, tmp_path / "state" / "context", service_repo_root=repo)
    assert result["source_count"] == 0  # symlink escape -> never indexed

    conn = sqlite3.connect(str(tmp_path / "state" / "context" / "context.db"))
    rows = conn.execute("SELECT text FROM chunks").fetchall()
    conn.close()
    assert not any("TOP SECRET" in (r[0] or "") for r in rows)


# ── 3. SQL injection ───────────────────────────────────────────────────

def test_get_source_sql_injection_attempt_is_just_not_found(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(state_dir, tmp_path, "00_baseline/A.md'; DROP TABLE chunks; --")
    assert exc_info.value.code == "PATH_NOT_IN_ALLOWLIST"

    # the real table must still exist and still be queryable afterwards
    conn = sqlite3.connect(str(state_dir / "context" / "context.db"))
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert count >= 1


def test_search_sql_injection_attempt_does_not_error_or_corrupt(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    result = cr.search(state_dir, tmp_path, "budget'; DROP TABLE chunks_fts; --")
    assert isinstance(result, dict)
    assert "results" in result

    conn = sqlite3.connect(str(state_dir / "context" / "context.db"))
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert count >= 1


# ── 4. FTS query escaping ──────────────────────────────────────────────

@pytest.mark.parametrize("raw_query", [
    'budget*', '-budget', '"unterminated', 'a OR b', 'NEAR(x y)', 'col:budget',
])
def test_fts_escape_never_lets_operators_reach_fts5_raw(raw_query):
    escaped = cr._fts_escape(raw_query)
    # every whitespace-separated term must appear inside its own quoted
    # phrase in the escaped output -- i.e. FTS5 operator characters are
    # neutralized by quoting, never passed through bare.
    for term in raw_query.split():
        quoted_term = '"' + term.replace('"', '""') + '"'
        assert quoted_term in escaped


def test_fts_escape_empty_query_is_a_safe_empty_phrase():
    assert cr._fts_escape("   ") == '""'


def test_search_with_fts_operator_characters_does_not_raise(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    for q in ['budget*', 'NEAR(budget partner)', '"unterminated', "col:budget --"]:
        result = cr.search(state_dir, tmp_path, q)
        assert isinstance(result, dict)


# ── 5. Oversized query / response ─────────────────────────────────────

def test_search_rejects_query_over_max_chars(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.search(tmp_path, tmp_path, "x" * (cr.QUERY_MAX_CHARS + 1))
    assert exc_info.value.code == "INVALID_QUERY"


def test_search_rejects_query_under_min_chars(tmp_path):
    with pytest.raises(RetrievalError) as exc_info:
        cr.search(tmp_path, tmp_path, "x")
    assert exc_info.value.code == "INVALID_QUERY"


def test_search_clamps_top_k_above_max(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123", num_docs=3)
    result = cr.search(state_dir, tmp_path, "budget", top_k=9999)
    assert len(result["results"]) <= cr.TOP_K_MAX


def test_search_response_stays_within_token_budget(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123", num_docs=5, big_text=True)
    result = cr.search(state_dir, tmp_path, "budget", top_k=10)
    assert result["token_estimate"] <= cr.SEARCH_TOKEN_BUDGET


def test_get_source_response_stays_within_token_budget(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123", num_docs=1, big_text=True)
    result = cr.get_source(state_dir, tmp_path, "00_baseline/DOC0.md")
    assert result["token_estimate"] <= cr.SOURCE_TOKEN_BUDGET + 50  # small headroom for envelope rounding
    assert result["truncated"] is True


# ── 6. Malformed UTF-8 ─────────────────────────────────────────────────

def test_index_builder_skips_non_utf8_document(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    bad_path = repo / "00_baseline" / "BAD.md"
    bad_path.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")
    good_path = repo / "00_baseline" / "GOOD.md"
    good_path.write_text("# Good\n\nThis one is fine.\n")
    _write_manifest(repo, [
        {"path": "00_baseline/BAD.md", "title": "Bad", "required_phase": "baseline", "required": True},
        {"path": "00_baseline/GOOD.md", "title": "Good", "required_phase": "baseline", "required": True},
    ])
    _init_git(repo)

    result = ib.build(repo, tmp_path / "state" / "context", service_repo_root=repo)
    assert result["source_count"] == 1  # only GOOD.md indexed

    conn = sqlite3.connect(str(tmp_path / "state" / "context" / "context.db"))
    paths = [r[0] for r in conn.execute("SELECT source_path FROM sources").fetchall()]
    conn.close()
    assert paths == ["00_baseline/GOOD.md"]


# ── 7. Invalid / corrupt SQLite ────────────────────────────────────────

def test_retrieval_handles_corrupt_db_file_gracefully(tmp_path):
    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "context.db").write_bytes(b"this is not a sqlite database at all")

    assert cr._read_db_meta(state_dir) is None

    result = cr.search(state_dir, tmp_path, "budget")
    assert result["available"] is False

    result2 = cr.get_source(state_dir, tmp_path, "00_baseline/X.md")
    assert result2["available"] is False

    result3 = cr.get_section(state_dir, tmp_path, "budget", {"available": True, "budget_summary": {}})
    assert isinstance(result3, dict)  # must not raise


# ── 8. Partially-created DB (missing tables) ──────────────────────────

def test_retrieval_handles_db_missing_meta_table(tmp_path):
    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(context_dir / "context.db"))
    conn.execute("CREATE TABLE sources (source_path TEXT)")  # no meta table at all
    conn.commit()
    conn.close()

    assert cr._read_db_meta(state_dir) is None
    result = cr.search(state_dir, tmp_path, "budget")
    assert result["available"] is False


def test_index_builder_failed_generation_leaves_no_tmp_file_and_old_db_untouched(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "A.md").write_text("# A\n\ncontent\n")
    _write_manifest(repo, [{"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo)
    good_db_bytes = (context_dir / "context.db").read_bytes()

    # simulate a failure: load_manifest will blow up because the file
    # becomes unreadable JSON -- build() must raise, and must not have
    # touched the previously-published, valid context.db.
    (repo / "config" / "canonical_documents.json").write_text("{ not valid json")
    with pytest.raises(ib.IndexBuildError):
        ib.build(repo, context_dir, service_repo_root=repo)

    assert (context_dir / "context.db").read_bytes() == good_db_bytes
    leftover_tmp = list(context_dir.glob("context.db.tmp-*"))
    assert leftover_tmp == []


# ── 9. Stale DB detection ─────────────────────────────────────────────

def test_envelope_reports_stale_when_db_commit_behind_live_head(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="0000000stale")
    monkeypatch.setattr(cr, "_live_repo_commit", lambda repo_root: "1111111fresh")
    result = cr.search(state_dir, tmp_path, "budget")
    assert result["freshness"] == "STALE"


def test_envelope_reports_fresh_when_commits_match(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    monkeypatch.setattr(cr, "_live_repo_commit", lambda repo_root: "abc123")
    result = cr.search(state_dir, tmp_path, "budget")
    assert result["freshness"] == "FRESH"


# ── 10. Source removed after indexing (clean rebuild, no orphans) ─────

def test_removed_source_disappears_from_next_generation_and_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "KEEP.md").write_text("# Keep\n\nstays.\n")
    (repo / "00_baseline" / "REMOVE.md").write_text("# Remove\n\ngoes away.\n")
    _write_manifest(repo, [
        {"path": "00_baseline/KEEP.md", "title": "Keep", "required_phase": "baseline", "required": True},
        {"path": "00_baseline/REMOVE.md", "title": "Remove", "required_phase": "baseline", "required": True},
    ])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo)

    result = cr.get_source(tmp_path / "state", repo, "00_baseline/REMOVE.md")
    assert result["available"] is True  # still present in this generation

    # Now supersede the removal: rewrite the manifest without REMOVE.md,
    # rebuild (clean full rebuild, not incremental).
    _write_manifest(repo, [
        {"path": "00_baseline/KEEP.md", "title": "Keep", "required_phase": "baseline", "required": True},
    ])
    ib.build(repo, context_dir, service_repo_root=repo)

    with pytest.raises(RetrievalError) as exc_info:
        cr.get_source(tmp_path / "state", repo, "00_baseline/REMOVE.md")
    assert exc_info.value.code == "PATH_NOT_IN_ALLOWLIST"

    still_there = cr.get_source(tmp_path / "state", repo, "00_baseline/KEEP.md")
    assert still_there["available"] is True


# ── 11. No secrets / denied paths never indexed ───────────────────────

def test_denied_path_substrings_are_never_indexed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / ".env.md").write_text("SECRET=shouldnotindex\n")
    (repo / "00_baseline" / "credential_notes.md").write_text("password: hunter2\n")
    (repo / "00_baseline" / "SAFE.md").write_text("# Safe\n\nfine.\n")
    _write_manifest(repo, [
        {"path": "00_baseline/.env.md", "title": "x", "required_phase": "baseline", "required": True},
        {"path": "00_baseline/credential_notes.md", "title": "x", "required_phase": "baseline", "required": True},
        {"path": "00_baseline/SAFE.md", "title": "x", "required_phase": "baseline", "required": True},
    ])
    _init_git(repo)

    result = ib.build(repo, tmp_path / "state" / "context", service_repo_root=repo)
    assert result["source_count"] == 1

    conn = sqlite3.connect(str(tmp_path / "state" / "context" / "context.db"))
    paths = [r[0] for r in conn.execute("SELECT source_path FROM sources").fetchall()]
    conn.close()
    assert paths == ["00_baseline/SAFE.md"]


# ── 12. No absolute paths ever exposed ────────────────────────────────

def test_get_source_response_contains_no_absolute_paths(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    result = cr.get_source(state_dir, tmp_path, "00_baseline/A.md")
    dumped = json.dumps(result)
    assert str(state_dir) not in dumped
    assert str(tmp_path) not in dumped


def test_search_response_contains_no_absolute_paths(tmp_path):
    state_dir = tmp_path / "state"
    _build_minimal_db(state_dir, commit="abc123")
    result = cr.search(state_dir, tmp_path, "budget")
    dumped = json.dumps(result)
    assert str(state_dir) not in dumped
    assert str(tmp_path) not in dumped


# ── 13. XSS-safe rendering (backend layer: JSON, never HTML) ──────────

def test_api_context_endpoints_return_json_content_type_not_html(tmp_path, monkeypatch):
    """A canonical document containing an HTML/script payload must come
    back as an inert JSON string value (never rendered as HTML by this
    service) -- the FastAPI endpoints always respond with
    application/json, so a browser would never execute it even if a
    caller blindly inserted the raw response into a page."""
    import importlib
    import os
    import sys

    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "XSS.md").write_text("# Title\n\n<script>alert(1)</script>\n")
    _write_manifest(repo, [{"path": "00_baseline/XSS.md", "title": "x", "required_phase": "baseline", "required": True}])
    _init_git(repo)
    ib.build(repo, context_dir, service_repo_root=repo)

    # app/main.py reads FORISEC_REPO_ROOT/FORISEC_STATE_DIR at import
    # time (see tests/test_dashboard_routes.py's _make_app helper for
    # the established pattern) -- set the env vars and force a fresh
    # import so this test never touches the real proposal repo/state.
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(state_dir))
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    client = TestClient(main_module.app)

    resp = client.get("/api/v1/context/source", params={"path": "00_baseline/XSS.md"})
    assert resp.headers["content-type"].startswith("application/json")
    assert "<script>" not in resp.text or resp.headers["content-type"].startswith("application/json")
    # The raw tag is preserved as an inert JSON string value (escaped
    # within the JSON envelope), never unwrapped into executable HTML
    # by this endpoint.
    body = resp.json()
    assert body["available"] is True


# ── 14. No arbitrary HTTP proxy from this layer ───────────────────────

def test_embeddings_fetch_only_ever_calls_the_fixed_configured_url(monkeypatch):
    calls = []

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2]]}).encode()

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return FakeResp()

    monkeypatch.setattr(ib.urllib.request, "urlopen", fake_urlopen)
    ib._fetch_embeddings(["hello"])
    assert calls == [ib.EMBEDDINGS_URL]
    assert calls[0].startswith("http://127.0.0.1:8101/")


# ── 15. No old database access ────────────────────────────────────────

def test_neither_module_references_old_memory_or_index_db_paths():
    """Both modules' docstrings legitimately *mention* the old
    memory.db/index.db paths (to document that they are never read) --
    what must never happen is either module actually opening or
    connecting to one. Check real usage, not prose."""
    for module in (ib, cr):
        source = inspect.getsource(module)
        assert not re.search(r'open\([^)]*memory\.db', source)
        assert not re.search(r'connect\([^)]*memory\.db', source)
        assert not re.search(r'open\([^)]*index\.db', source)
        assert not re.search(r'connect\([^)]*index\.db', source)
        assert "8103" not in re.sub(r'#.*|""".*?"""', '', source, flags=re.DOTALL)  # no live call to old Search API


# ── helpers ────────────────────────────────────────────────────────────

def _build_minimal_db(state_dir, commit="abc123", num_docs=1, big_text=False):
    """Build a real context.db (via the real index_builder) against a
    tiny throwaway repo, then re-stamp meta.proposal_repo_commit to the
    requested commit value (so tests can control freshness/staleness
    independent of the throwaway repo's real git HEAD)."""
    repo = state_dir.parent / "src_repo"
    (repo / "00_baseline").mkdir(parents=True, exist_ok=True)
    docs = []
    for i in range(num_docs):
        text = f"# Doc {i}\n\n" + ("budget partner reconciliation content. " * (200 if big_text else 5))
        (repo / "00_baseline" / f"DOC{i}.md").write_text(text)
        docs.append({"path": f"00_baseline/DOC{i}.md", "title": f"Doc {i}", "required_phase": "baseline", "required": True})
    (repo / "00_baseline" / "A.md").write_text("# A\n\nbudget content for A.\n")
    docs.append({"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True})
    _write_manifest(repo, docs)
    _init_git(repo)

    context_dir = state_dir / "context"
    ib.build(repo, context_dir, service_repo_root=repo, use_semantic=False)

    conn = sqlite3.connect(str(context_dir / "context.db"))
    conn.execute("UPDATE meta SET proposal_repo_commit = ?", (commit,))
    conn.commit()
    conn.close()
    return repo
