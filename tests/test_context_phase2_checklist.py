"""
Remaining FINAL PHASE 2 section-10 checklist items not already covered
by tests/test_context_builder.py, tests/test_context_security.py, or
tests/test_generation_marker.py: WP1-WP6 mapping, index-time superseded
classification, semantic fallback, source hash, read-only enforcement,
the combined 15k bootstrap+retrieval envelope, and the /health/live +
/health/ready endpoints.
"""
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from context import index_builder as ib
from context import retrieval as cr
from pipeline import context_builder as cb


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


def _make_app(repo_root: Path, state_dir: Path):
    os.environ["FORISEC_REPO_ROOT"] = str(repo_root)
    os.environ["FORISEC_STATE_DIR"] = str(state_dir)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    return main_module.app


# ── WP1-WP6 mapping ────────────────────────────────────────────────────

def test_wp_mapping_reads_real_wp_documents(tmp_path):
    repo = tmp_path / "repo"
    wp_dir = repo / "01_work_packages"
    wp_dir.mkdir(parents=True)
    (wp_dir / "WP1_PROJECT_MANAGEMENT.md").write_text(
        "# WP1 Project Management\n\n**Lead:** FORITECH\n**Status:** ONGOING\n\n"
        "## 1. Role and Scope\n\nCoordinates the whole consortium.\n\n"
        "## T1.1 Kickoff\n\n## T1.2 Reporting\n"
    )
    result = cb._work_package_summary_v2(repo)
    wp1 = next(e for e in result if e["wp_id"] == "WP1")
    assert wp1["available"] is True
    assert wp1["title"] == "WP1 Project Management"
    assert wp1["lead"] == "FORITECH"
    assert wp1["status"] == "ONGOING"
    assert wp1["purpose_summary"] == "Coordinates the whole consortium."
    assert wp1["task_count"] == 2

    # WP2-WP6 documents are simply absent in this fixture -- must be
    # honestly UNKNOWN/unavailable, never fabricated.
    wp2 = next(e for e in result if e["wp_id"] == "WP2")
    assert wp2["available"] is False
    assert wp2["title"] == "UNKNOWN"


def test_wp_mapping_dependencies_reference_other_wps(tmp_path):
    repo = tmp_path / "repo"
    wp_dir = repo / "01_work_packages"
    wp_dir.mkdir(parents=True)
    (wp_dir / "WP2_PQC_PLATFORM.md").write_text(
        "# WP2 PQC Platform\n\n**Lead:** FORITECH\n**Status:** ONGOING\n\n"
        "## 1. Role and Scope\n\nBuilds the crypto platform used by WP3 and WP1.\n"
    )
    result = cb._work_package_summary_v2(repo)
    wp2 = next(e for e in result if e["wp_id"] == "WP2")
    assert wp2["dependencies"] == ["WP1", "WP3"]


# ── Index-time superseded classification ──────────────────────────────

def test_index_builder_marks_superseded_source_from_canonical_status(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "OLD.md").write_text(
        "<!-- CANONICAL_STATUS: SUPERSEDED -->\n# Old\n\nreplaced content.\n"
    )
    (repo / "00_baseline" / "CURRENT.md").write_text(
        "<!-- CANONICAL_STATUS: FROZEN -->\n# Current\n\nlive content.\n"
    )
    _write_manifest(repo, [
        {"path": "00_baseline/OLD.md", "title": "Old", "required_phase": "baseline", "required": True},
        {"path": "00_baseline/CURRENT.md", "title": "Current", "required_phase": "baseline", "required": True},
    ])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo)

    conn = sqlite3.connect(str(context_dir / "context.db"))
    rows = dict(conn.execute("SELECT source_path, superseded FROM sources").fetchall())
    conn.close()
    assert rows["00_baseline/OLD.md"] == 1
    assert rows["00_baseline/CURRENT.md"] == 0

    result = cr.get_source(tmp_path / "state", repo, "00_baseline/OLD.md")
    assert result["source"]["superseded"] is True


# ── Semantic fallback ──────────────────────────────────────────────────

def test_search_falls_back_to_lexical_when_embeddings_worker_unreachable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "A.md").write_text("# A\n\nbudget partner reconciliation.\n")
    _write_manifest(repo, [{"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    # Force the "worker unreachable" branch deterministically -- this
    # host may actually be running a real embeddings worker on :8101
    # (see the wider FORISEC deployment), so asserting semantic
    # unavailability must not depend on the ambient environment.
    monkeypatch.setattr(ib, "_fetch_embeddings", lambda texts: None)

    context_dir = tmp_path / "state" / "context"
    result = ib.build(repo, context_dir, service_repo_root=repo, use_semantic=True)
    assert result["semantic_available"] is False
    assert result["index_model_version"] == ib.INDEX_MODEL_VERSION_LEXICAL

    monkeypatch.setattr(cr, "_fetch_embeddings", lambda texts: None)
    search_result = cr.search(tmp_path / "state", repo, "budget")
    assert search_result["semantic_used"] is False
    assert len(search_result["results"]) >= 1  # lexical (FTS5) still works


def test_search_reports_semantic_used_honestly_when_worker_available(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "A.md").write_text("# A\n\nbudget partner reconciliation.\n")
    _write_manifest(repo, [{"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    def fake_fetch_embeddings(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(ib, "_fetch_embeddings", fake_fetch_embeddings)
    monkeypatch.setattr(ib, "EMBEDDINGS_DIM", 3)

    context_dir = tmp_path / "state" / "context"
    result = ib.build(repo, context_dir, service_repo_root=repo, use_semantic=True)
    assert result["semantic_available"] is True
    assert result["index_model_version"] == ib.INDEX_MODEL_VERSION_SEMANTIC

    monkeypatch.setattr(cr, "_fetch_embeddings", fake_fetch_embeddings)
    search_result = cr.search(tmp_path / "state", repo, "budget")
    assert search_result["semantic_used"] is True


# ── Source hash ─────────────────────────────────────────────────────────

def test_source_hash_matches_real_file_content(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    content = "# A\n\nexact content whose hash we will verify.\n"
    (repo / "00_baseline" / "A.md").write_text(content)
    _write_manifest(repo, [{"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo)

    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result = cr.get_source(tmp_path / "state", repo, "00_baseline/A.md")
    assert result["source"]["source_hash"] == expected_hash


# ── Read-only enforcement ───────────────────────────────────────────────

def test_retrieval_connection_is_opened_read_only_and_cannot_write(tmp_path):
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    (repo / "00_baseline" / "A.md").write_text("# A\n\nbudget content.\n")
    _write_manifest(repo, [{"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True}])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo)
    db_path = context_dir / "context.db"
    before_bytes = db_path.read_bytes()

    conn = cr._open_db_readonly(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO sources (source_path, source_hash, source_type, indexed_at, token_estimate) "
                     "VALUES ('x', 'y', 'md', 't', 1)")
    conn.close()

    # Running normal read operations through the module must also never
    # mutate the file on disk.
    cr.search(tmp_path / "state", repo, "budget")
    cr.get_source(tmp_path / "state", repo, "00_baseline/A.md")
    cr.get_section(tmp_path / "state", repo, "budget", {"available": False})
    assert db_path.read_bytes() == before_bytes


# ── Combined 15k envelope (bootstrap + one retrieval call) ─────────────

def test_bootstrap_plus_search_response_stays_within_combined_envelope(tmp_path):
    """bootstrap (<=6000) + one retrieval response (<=3000 for
    search/source, <=2500 for section) must never combine to more than
    the 15000-token total envelope the spec caps retrieval usage at."""
    repo = tmp_path / "repo"
    (repo / "00_baseline").mkdir(parents=True)
    for i in range(5):
        (repo / "00_baseline" / f"DOC{i}.md").write_text(
            f"# Doc {i}\n\n" + ("budget partner reconciliation content. " * 150)
        )
    _write_manifest(repo, [
        {"path": f"00_baseline/DOC{i}.md", "title": f"Doc {i}", "required_phase": "baseline", "required": True}
        for i in range(5)
    ])
    _init_git(repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(repo, context_dir, service_repo_root=repo, use_semantic=False)

    search_result = cr.search(tmp_path / "state", repo, "budget", top_k=10)
    # Bootstrap isn't buildable from this minimal fixture (no docs_controller
    # etc. state files), so use the documented hard ceiling (6000) directly --
    # the real bootstrap ceiling is covered by tests/test_context_builder.py.
    assert 6000 + search_result["token_estimate"] <= 15000


# ── /health/live and /health/ready ──────────────────────────────────────

def test_health_live_is_always_ok_even_with_no_state(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "live": True}


def test_health_ready_is_not_ready_with_no_bootstrap_or_index(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["status"] == "not_ready"
    assert body["reasons"]
    # never an absolute path in the response
    dumped = json.dumps(body)
    assert str(state_dir) not in dumped
    assert str(fake_repo) not in dumped


def test_health_ready_is_ready_when_bootstrap_and_index_agree_and_are_fresh(fake_repo, state_dir):
    from agents.common import atomic_write_json, get_repo_commit

    commit = get_repo_commit(fake_repo)
    # Build a minimal, schema-valid bootstrap the same way
    # test_generation_marker.py does.
    import json as _json
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "project_context_state.schema.json"
    schema = _json.loads(schema_path.read_text())

    def default_for(subschema):
        t = subschema.get("type")
        if isinstance(t, list):
            t = t[0]
        if t == "object":
            return {f: default_for(subschema.get("properties", {}).get(f, {})) for f in subschema.get("required", [])}
        if t == "array":
            return []
        if t in ("integer", "number"):
            return 0
        if t == "boolean":
            return False
        return "UNKNOWN"

    bundle = {f: default_for(schema["properties"].get(f, {})) for f in schema["required"]}
    bundle.update({
        "repo_commit": commit, "service_commit": "abc1234", "freshness": "FRESH",
        "schema_version": schema["properties"].get("schema_version", {}).get("const", "1.0"),
        "context_model_version": "2.0", "generation_id": "11111111-1111-1111-1111-111111111111",
        "generated_at": "2026-07-21T00:00:00+00:00",
        "token_estimate": {"characters": 0, "estimated_tokens": 0, "method": "test"},
    })
    atomic_write_json(state_dir / "project_context_state.json", bundle)

    context_dir = state_dir / "context"
    (repo := fake_repo)
    ib.build(repo, context_dir, service_repo_root=repo, use_semantic=False)
    conn = sqlite3.connect(str(context_dir / "context.db"))
    conn.execute("UPDATE meta SET proposal_repo_commit = ?", (commit,))
    conn.commit()
    conn.close()

    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/health/ready")
    body = resp.json()
    assert body["ready"] is True
    assert body["status"] == "ready"
    assert body["mixed_generation"] is False


def test_health_ready_flags_mixed_generation(fake_repo, state_dir):
    from agents.common import atomic_write_json, get_repo_commit
    import json as _json

    commit = get_repo_commit(fake_repo)
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "project_context_state.schema.json"
    schema = _json.loads(schema_path.read_text())

    def default_for(subschema):
        t = subschema.get("type")
        if isinstance(t, list):
            t = t[0]
        if t == "object":
            return {f: default_for(subschema.get("properties", {}).get(f, {})) for f in subschema.get("required", [])}
        if t == "array":
            return []
        if t in ("integer", "number"):
            return 0
        if t == "boolean":
            return False
        return "UNKNOWN"

    bundle = {f: default_for(schema["properties"].get(f, {})) for f in schema["required"]}
    bundle.update({
        "repo_commit": commit, "service_commit": "abc1234", "freshness": "FRESH",
        "schema_version": schema["properties"].get("schema_version", {}).get("const", "1.0"),
        "context_model_version": "2.0", "generation_id": "11111111-1111-1111-1111-111111111111",
        "generated_at": "2026-07-21T00:00:00+00:00",
        "token_estimate": {"characters": 0, "estimated_tokens": 0, "method": "test"},
    })
    atomic_write_json(state_dir / "project_context_state.json", bundle)

    context_dir = state_dir / "context"
    ib.build(fake_repo, context_dir, service_repo_root=fake_repo, use_semantic=False)
    conn = sqlite3.connect(str(context_dir / "context.db"))
    conn.execute("UPDATE meta SET proposal_repo_commit = ?", ("0000000differentcommit",))
    conn.commit()
    conn.close()

    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/health/ready")
    body = resp.json()
    assert body["ready"] is False
    assert body["mixed_generation"] is True
