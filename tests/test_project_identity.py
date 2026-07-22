"""
Tests for context/identity.py's project-identity lock and its
enforcement across index_builder.py, retrieval.py, and the
forisec-cl3-dashboard MCP connector proxy tools.

Context: this dashboard/context system must never be confused with
sibling systems -- foritech-os (a different legacy control-plane
codebase) or foritech-secure-system (Foritech's own product repo).
config/canonical_documents.json's own "project" field is only the
short display label "FORISEC" and is NOT a safe unique identifier on
its own (multiple Foritech systems could plausibly reuse that label).
This project's real identity is the fixed pair
(project_id="forisec-cl3-2026", context_namespace="forisec_cl3_2026"),
defined once in context/identity.py and never derived from any file.
"""
import json
import sqlite3
import subprocess

import pytest

from context import identity
from context import index_builder as ib
from context import retrieval as cr


def _write_manifest(repo, project_label="FORISEC"):
    manifest = {
        "manifest_version": "test", "project": project_label,
        "current_phase": "baseline", "phases": {"baseline": {"depends_on": []}},
        "documents": [],
    }
    (repo / "config").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "canonical_documents.json").write_text(json.dumps(manifest))


def _init_git(repo):
    subprocess.run(["git", "init", "-q"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo)


def _fake_service_repo(tmp_path):
    service_repo = tmp_path / "service_repo"
    (service_repo / "app").mkdir(parents=True)
    (service_repo / "app" / "main.py").write_text('"""fake."""\ndef f():\n    pass\n')
    return service_repo


def _built_context_db(tmp_path, project_label="FORISEC"):
    proposal_repo = tmp_path / "proposal_repo"
    proposal_repo.mkdir()
    _write_manifest(proposal_repo, project_label)
    _init_git(proposal_repo)

    service_repo = _fake_service_repo(tmp_path)
    _init_git(service_repo)

    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    ib.build(proposal_repo, context_dir, service_repo_root=service_repo, use_semantic=False)
    return state_dir, proposal_repo, context_dir / "context.db"


# ── identity.py constants themselves ───────────────────────────────────

def test_identity_constants_match_the_locked_spec():
    assert identity.PROJECT_ID == "forisec-cl3-2026"
    assert identity.PROJECT_SHORT_NAME == "FORISEC"
    assert identity.PROJECT_DISPLAY_NAME == "FORISEC — HORIZON-CL3-2026-02-CS-ECCC-01"
    assert identity.CONTEXT_NAMESPACE == "forisec_cl3_2026"
    assert identity.MCP_TOOL_PREFIX == "forisec_cl3_2026_context_"


def test_identity_matches_requires_both_fields():
    assert identity.identity_matches(identity.PROJECT_ID, identity.CONTEXT_NAMESPACE) is True
    assert identity.identity_matches(identity.PROJECT_ID, "some-other-namespace") is False
    assert identity.identity_matches("some-other-project", identity.CONTEXT_NAMESPACE) is False
    assert identity.identity_matches(None, None) is False


def test_identity_matches_rejects_bare_short_label_as_project_id():
    """The short display label "FORISEC" alone must never be accepted as
    if it were the real project_id -- this is the exact footgun the
    identity lock exists to prevent."""
    assert identity.identity_matches("FORISEC", identity.CONTEXT_NAMESPACE) is False


# ── index_builder.py: identity is fixed, never manifest-derived ────────

def test_build_writes_fixed_identity_into_meta_regardless_of_manifest_label(tmp_path):
    state_dir, _repo, db_path = _built_context_db(tmp_path, project_label="something-else-entirely")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT project_id, project_short_name, project_display_name, context_namespace FROM meta"
    ).fetchone()
    conn.close()
    assert row == (identity.PROJECT_ID, identity.PROJECT_SHORT_NAME,
                   identity.PROJECT_DISPLAY_NAME, identity.CONTEXT_NAMESPACE)


def test_build_stamps_identity_onto_sources_chunks_and_repo_map_rows(tmp_path):
    state_dir, _repo, db_path = _built_context_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    for table in ("sources", "chunks", "repo_map"):
        rows = conn.execute(f"SELECT DISTINCT project_id, context_namespace FROM {table}").fetchall()
        for project_id, context_namespace in rows:
            assert project_id == identity.PROJECT_ID
            assert context_namespace == identity.CONTEXT_NAMESPACE
    conn.close()


# ── retrieval.py: reject any mismatched context.db ──────────────────────

def _restamp_meta(db_path, **fields):
    conn = sqlite3.connect(str(db_path))
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE meta SET {set_clause}", tuple(fields.values()))
    conn.commit()
    conn.close()


def test_retrieval_envelope_reports_this_projects_identity_when_healthy(tmp_path):
    state_dir, repo_root, _db_path = _built_context_db(tmp_path)
    result = cr.get_repo_map(state_dir, repo_root)
    assert result["project_id"] == identity.PROJECT_ID
    assert result["context_namespace"] == identity.CONTEXT_NAMESPACE
    assert result["available"] is True


def test_search_rejects_context_db_with_foreign_project_id(tmp_path):
    state_dir, repo_root, db_path = _built_context_db(tmp_path)
    _restamp_meta(db_path, project_id="foritech-os")

    result = cr.search(state_dir, repo_root, "budget")
    assert result["available"] is False
    assert result["results"] == []
    assert "does not match" in result["reason"]
    # Fixed constants are still reported even though the on-disk db is rejected.
    assert result["project_id"] == identity.PROJECT_ID
    assert result["context_namespace"] == identity.CONTEXT_NAMESPACE


def test_get_source_rejects_context_db_with_foreign_context_namespace(tmp_path):
    state_dir, repo_root, db_path = _built_context_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    source_path = conn.execute("SELECT source_path FROM sources LIMIT 1").fetchone()
    conn.close()
    _restamp_meta(db_path, context_namespace="foritech_secure_system")

    if source_path is None:
        pytest.skip("fixture produced no sources to fetch")
    result = cr.get_source(state_dir, repo_root, source_path[0])
    assert result["available"] is False
    assert result["chunks"] == []


def test_get_repo_map_rejects_context_db_with_foreign_identity(tmp_path):
    state_dir, repo_root, db_path = _built_context_db(tmp_path)
    _restamp_meta(db_path, project_id="foritech-os", context_namespace="foritech_os")

    result = cr.get_repo_map(state_dir, repo_root)
    assert result["available"] is False
    assert result["files"] == []


def test_get_section_rejects_bootstrap_with_foreign_identity(tmp_path):
    state_dir, repo_root, _db_path = _built_context_db(tmp_path)
    foreign_bootstrap = {
        "available": True, "project_id": "foritech-os", "context_namespace": "foritech_os",
    }
    result = cr.get_section(state_dir, repo_root, "budget", foreign_bootstrap)
    assert result["summary"] is None
    assert "does not match" in result["reason"]


def test_get_section_accepts_bootstrap_with_correct_identity(tmp_path):
    state_dir, repo_root, _db_path = _built_context_db(tmp_path)
    own_bootstrap = {
        "available": True, "project_id": identity.PROJECT_ID,
        "context_namespace": identity.CONTEXT_NAMESPACE,
        "budget_summary": {"total_pm": 42}, "source_map": {},
    }
    result = cr.get_section(state_dir, repo_root, "budget", own_bootstrap)
    assert result["summary"] == {"total_pm": 42}


# ── MCP connector: fixed, unambiguous tool names ───────────────────────

def test_mcp_tool_prefix_constant_is_unambiguous():
    assert identity.MCP_TOOL_PREFIX == "forisec_cl3_2026_context_"
    assert "foritech" not in identity.MCP_TOOL_PREFIX
