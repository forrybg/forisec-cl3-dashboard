"""
Tests for context/generation_marker.py -- the refresh_agents.sh step 12
that confirms project_context_state.json (step 10) and context.db
(step 11) are schema-valid and bound to the SAME proposal repo commit
before anything is treated as a complete, publishable generation.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from agents.common import atomic_write_json, get_repo_commit
from context import generation_marker as gm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "contracts" / "project_context_state.schema.json"


def _default_for_subschema(subschema: dict):
    """Build a minimal value satisfying subschema's own required/
    properties, recursively, rather than guessing a bare string -- this
    is what broke the first version of this fixture on object-typed
    fields like architecture_summary/current_state/token_estimate."""
    t = subschema.get("type")
    if isinstance(t, list):
        t = t[0]
    if t == "object":
        obj = {}
        for sub_req_field in subschema.get("required", []):
            sub_props = subschema.get("properties", {})
            obj[sub_req_field] = _default_for_subschema(sub_props.get(sub_req_field, {}))
        return obj
    if t == "array":
        return []
    if t == "integer" or t == "number":
        return 0
    if t == "boolean":
        return False
    # string (or null-unioned string, or untyped) -- "UNKNOWN" is always
    # a valid string value and matches the codebase's own convention for
    # "fact not safely extractable."
    return "UNKNOWN"


def _valid_bootstrap(repo_commit: str) -> dict:
    """A minimal but schema-valid bootstrap bundle. Built field-by-field
    from the real schema's required fields and their declared types
    (recursing into nested object/array schemas), so this test breaks
    loudly if the contract changes shape rather than silently drifting."""
    schema = json.loads(SCHEMA_PATH.read_text())
    required = schema["required"]
    properties = schema["properties"]
    bundle = {field: _default_for_subschema(properties.get(field, {})) for field in required}

    bundle["repo_commit"] = repo_commit
    bundle["service_commit"] = "abc1234"
    bundle["freshness"] = "FRESH"
    bundle["schema_version"] = properties.get("schema_version", {}).get("const", "1.0")
    bundle["context_model_version"] = "2.0"
    bundle["generation_id"] = "11111111-1111-1111-1111-111111111111"
    bundle["generated_at"] = "2026-07-21T00:00:00+00:00"
    bundle["token_estimate"] = {"characters": 0, "estimated_tokens": 0, "method": "test"}
    return bundle


def _write_context_db(state_dir: Path, repo_commit: str, service_commit: str = "abc1234",
                       generation_id: str = "22222222-2222-2222-2222-222222222222") -> Path:
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    db_path = context_dir / "context.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE meta (
            schema_version TEXT NOT NULL, index_model_version TEXT NOT NULL,
            project_id TEXT NOT NULL, proposal_repo_commit TEXT, service_commit TEXT,
            generated_at TEXT NOT NULL, generation_id TEXT NOT NULL,
            source_count INTEGER NOT NULL, chunk_count INTEGER NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("1.0", "1.0-lexical", "forisec-cl3", repo_commit, service_commit,
         "2026-07-21T00:00:00+00:00", generation_id, 1, 1),
    )
    conn.commit()
    conn.close()
    return db_path


def test_ok_when_bootstrap_and_db_agree_on_commit(fake_repo, state_dir):
    commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(commit))
    _write_context_db(state_dir, commit)

    result = gm.validate(fake_repo, state_dir)

    assert result["ok"] is True
    assert result["reasons"] == []
    assert result["repo_commit"] == commit
    marker_path = state_dir / gm.MARKER_FILENAME
    assert marker_path.exists()
    on_disk = json.loads(marker_path.read_text())
    assert on_disk["ok"] is True


def test_rejects_mixed_generation_different_commits(fake_repo, state_dir):
    """The exact scenario the spec calls out: step 10 wrote against one
    commit, step 11 ran later against a newer commit. Must be flagged,
    never silently accepted as complete."""
    commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(commit))
    _write_context_db(state_dir, repo_commit="0000000deadbeef")

    result = gm.validate(fake_repo, state_dir)

    assert result["ok"] is False
    assert any("mixed generation" in r for r in result["reasons"])


def test_missing_bootstrap_is_not_ok(fake_repo, state_dir):
    _write_context_db(state_dir, get_repo_commit(fake_repo))
    result = gm.validate(fake_repo, state_dir)
    assert result["ok"] is False
    assert any("project_context_state.json" in r for r in result["reasons"])


def test_missing_context_db_is_not_ok(fake_repo, state_dir):
    commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(commit))
    result = gm.validate(fake_repo, state_dir)
    assert result["ok"] is False
    assert any("context.db missing" in r for r in result["reasons"])


def test_schema_invalid_bootstrap_is_not_ok(fake_repo, state_dir):
    commit = get_repo_commit(fake_repo)
    broken = _valid_bootstrap(commit)
    del broken["canonical_sources"]  # required field removed -> schema violation
    atomic_write_json(state_dir / "project_context_state.json", broken)
    _write_context_db(state_dir, commit)

    result = gm.validate(fake_repo, state_dir)
    assert result["ok"] is False
    assert any("schema validation" in r for r in result["reasons"])


def test_stale_bootstrap_behind_live_repo_head_is_not_ok(fake_repo, state_dir):
    """Bootstrap and context.db agree with each other but both are
    behind the live repo HEAD (a new commit landed after both were
    generated) -- this must also be caught, not just internal
    bootstrap/db agreement."""
    stale_commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(stale_commit))
    _write_context_db(state_dir, stale_commit)

    # advance the fake repo's HEAD past the commit the bundle was built against
    (fake_repo / "00_baseline" / "B.md").write_text("more content\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=fake_repo)
    subprocess.run(["git", "commit", "-q", "-m", "second commit"], cwd=fake_repo)

    result = gm.validate(fake_repo, state_dir)
    assert result["ok"] is False
    assert any("does not match live HEAD" in r for r in result["reasons"])


def test_failed_generation_leaves_old_marker_readable(fake_repo, state_dir):
    """A failed validate() call still writes a marker (ok=false) via
    atomic_write_json rather than crashing or leaving a half-written
    file -- refresh_agents.sh relies on this file always being valid
    JSON so health/ready can report STALE/DEGRADED off of it."""
    commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(commit))
    # no context.db written at all -> guaranteed failure path
    result = gm.validate(fake_repo, state_dir)
    assert result["ok"] is False

    marker_path = state_dir / gm.MARKER_FILENAME
    on_disk = json.loads(marker_path.read_text())
    assert on_disk["ok"] is False
    assert on_disk["reasons"]


def test_main_exits_nonzero_on_invalid_generation(fake_repo, state_dir, monkeypatch, capsys):
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(state_dir))
    # bootstrap missing entirely
    with pytest.raises(SystemExit) as exc_info:
        gm.main()
    assert exc_info.value.code != 0


def test_main_exits_zero_on_valid_generation(fake_repo, state_dir, monkeypatch):
    commit = get_repo_commit(fake_repo)
    atomic_write_json(state_dir / "project_context_state.json", _valid_bootstrap(commit))
    _write_context_db(state_dir, commit)
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(state_dir))
    with pytest.raises(SystemExit) as exc_info:
        gm.main()
    assert exc_info.value.code == 0


def test_main_config_error_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("FORISEC_REPO_ROOT", raising=False)
    monkeypatch.delenv("FORISEC_STATE_DIR", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        gm.main()
    assert exc_info.value.code == 1
