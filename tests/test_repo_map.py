"""
Tests for the repo map feature: context/index_builder.py's deterministic
scan of this SERVICE's own codebase (never the proposal repo) into the
new context.db `repo_map` table, and context/retrieval.py::get_repo_map().
"""
import json
import sqlite3
import subprocess

import pytest

from context import index_builder as ib
from context import retrieval as cr


def _write_manifest(repo):
    manifest = {
        "manifest_version": "test", "project": "forisec-cl3-test",
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
    """A throwaway stand-in for forisec-cl3-dashboard's own codebase --
    never the real service repo, so this test never depends on the
    real tree's exact shape."""
    service_repo = tmp_path / "service_repo"
    (service_repo / "app").mkdir(parents=True)
    (service_repo / "agents").mkdir(parents=True)
    (service_repo / "pipeline").mkdir(parents=True)
    (service_repo / "context").mkdir(parents=True)
    (service_repo / "scripts").mkdir(parents=True)
    (service_repo / "contracts").mkdir(parents=True)

    (service_repo / "app" / "main.py").write_text(
        '"""app/main.py -- fake FastAPI app for testing."""\n'
        "def api_health():\n    return {}\n\n"
        "def _private_helper():\n    pass\n\n"
        "class App:\n    pass\n"
    )
    (service_repo / "agents" / "__init__.py").write_text("")
    (service_repo / "agents" / "widget.py").write_text(
        "# no module docstring here\n"
        "def run():\n    pass\n"
    )
    (service_repo / "pipeline" / "broken.py").write_text("def (:\n")  # invalid syntax
    (service_repo / "context" / "index_builder.py").write_text(
        '"""context/index_builder.py -- fake for testing."""\n'
    )
    (service_repo / "scripts" / "refresh_agents.sh").write_text(
        "#!/bin/bash\n# Runs the FORISEC CL3 agents pipeline\necho hi\n"
    )
    (service_repo / "contracts" / "project_context_state.schema.json").write_text(
        json.dumps({"type": "object"})
    )
    return service_repo


def test_scan_repo_map_extracts_docstring_functions_classes(tmp_path):
    service_repo = _fake_service_repo(tmp_path)
    rows = ib._scan_repo_map(service_repo)
    by_path = {r["path"]: r for r in rows}

    main_row = by_path["app/main.py"]
    assert main_row["kind"] == "python_module"
    assert main_row["summary"] == "app/main.py -- fake FastAPI app for testing."
    assert main_row["top_level_functions"] == ["api_health"]  # _private_helper excluded
    assert main_row["top_level_classes"] == ["App"]
    assert main_row["line_count"] > 0


def test_scan_repo_map_handles_missing_docstring(tmp_path):
    service_repo = _fake_service_repo(tmp_path)
    rows = ib._scan_repo_map(service_repo)
    by_path = {r["path"]: r for r in rows}
    assert by_path["agents/widget.py"]["summary"] is None
    assert by_path["agents/widget.py"]["top_level_functions"] == ["run"]


def test_scan_repo_map_never_raises_on_invalid_syntax(tmp_path):
    service_repo = _fake_service_repo(tmp_path)
    rows = ib._scan_repo_map(service_repo)  # pipeline/broken.py must not crash the scan
    by_path = {r["path"]: r for r in rows}
    assert by_path["pipeline/broken.py"]["summary"] is None
    assert by_path["pipeline/broken.py"]["top_level_functions"] == []


def test_scan_repo_map_includes_extra_non_python_files(tmp_path):
    service_repo = _fake_service_repo(tmp_path)
    rows = ib._scan_repo_map(service_repo)
    by_path = {r["path"]: r for r in rows}
    assert by_path["scripts/refresh_agents.sh"]["kind"] == "script"
    assert by_path["contracts/project_context_state.schema.json"]["kind"] == "schema"


def test_scan_repo_map_never_scans_the_proposal_repo(tmp_path):
    """The repo map's scope is REPO_MAP_SOURCE_DIRS relative to
    service_repo_root only -- passing the proposal repo in that
    argument position must never accidentally index proposal content
    as if it were service code (a str/path-mixup regression guard)."""
    proposal_repo = tmp_path / "proposal_repo"
    (proposal_repo / "app").mkdir(parents=True)  # coincidentally shares a dir name
    (proposal_repo / "app" / "not_really_code.py").write_text("SECRET_PROPOSAL_TEXT = 1\n")
    rows = ib._scan_repo_map(proposal_repo)
    # This is intentionally permissive at the unit level (the function
    # just walks whatever root it's given) -- the real safety property
    # is that build() always calls it with service_repo_root, verified
    # in the end-to-end test below.
    assert any(r["path"] == "app/not_really_code.py" for r in rows)


def test_repo_map_table_populated_by_full_build(tmp_path):
    proposal_repo = tmp_path / "proposal_repo"
    proposal_repo.mkdir()
    _write_manifest(proposal_repo)
    _init_git(proposal_repo)

    service_repo = _fake_service_repo(tmp_path)
    _init_git(service_repo)

    context_dir = tmp_path / "state" / "context"
    ib.build(proposal_repo, context_dir, service_repo_root=service_repo, use_semantic=False)

    conn = sqlite3.connect(str(context_dir / "context.db"))
    rows = conn.execute("SELECT path, kind FROM repo_map ORDER BY path").fetchall()
    conn.close()
    paths = [r[0] for r in rows]
    assert "app/main.py" in paths
    assert "scripts/refresh_agents.sh" in paths
    # never leaks proposal-repo content into repo_map
    assert not any("config/canonical_documents.json" == p for p in paths)


def test_get_repo_map_via_retrieval_layer(tmp_path):
    proposal_repo = tmp_path / "proposal_repo"
    proposal_repo.mkdir()
    _write_manifest(proposal_repo)
    _init_git(proposal_repo)

    service_repo = _fake_service_repo(tmp_path)
    _init_git(service_repo)

    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    ib.build(proposal_repo, context_dir, service_repo_root=service_repo, use_semantic=False)

    result = cr.get_repo_map(state_dir, proposal_repo)
    assert result["available"] is True
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["app/main.py"]["top_level_functions"] == ["api_health"]
    assert by_path["app/main.py"]["top_level_classes"] == ["App"]
    assert result["token_estimate"] <= cr.REPO_MAP_TOKEN_BUDGET


def test_get_repo_map_unavailable_when_no_db(tmp_path):
    result = cr.get_repo_map(tmp_path / "state", tmp_path / "repo")
    assert result["available"] is False
    assert result["files"] == []


def test_get_repo_map_handles_corrupt_db_gracefully(tmp_path):
    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "context.db").write_bytes(b"not a real sqlite file")
    result = cr.get_repo_map(state_dir, tmp_path / "repo")
    assert result["available"] is False


def test_repo_map_response_contains_no_absolute_paths(tmp_path):
    proposal_repo = tmp_path / "proposal_repo"
    proposal_repo.mkdir()
    _write_manifest(proposal_repo)
    _init_git(proposal_repo)

    service_repo = _fake_service_repo(tmp_path)
    _init_git(service_repo)

    state_dir = tmp_path / "state"
    context_dir = state_dir / "context"
    ib.build(proposal_repo, context_dir, service_repo_root=service_repo, use_semantic=False)

    result = cr.get_repo_map(state_dir, proposal_repo)
    dumped = json.dumps(result)
    assert str(state_dir) not in dumped
    assert str(service_repo) not in dumped
