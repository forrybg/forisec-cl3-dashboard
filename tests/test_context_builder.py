import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from pipeline import context_builder as cb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "contracts" / "project_context_state.schema.json"


def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def _write_all_state(state_dir: Path, repo_commit: str, overrides: dict | None = None):
    """Writes a minimal, valid set of the six consumed state files, all
    stamped with the same repo_commit unless overridden."""
    base = {
        "docs": {"schema_version": "1.0", "agent_id": "docs_controller", "repo_commit": repo_commit,
                  "run_timestamp": "t", "status": "completed", "overall_status": "OK",
                  "draft_count": 0, "blocked_count": 0, "documents": [], "findings": []},
        "budget": {"schema_version": "1.0", "agent_id": "budget_reader", "repo_commit": repo_commit,
                   "run_timestamp": "t", "status": "completed", "available": True,
                   "total_pm": 100, "total_eur": 1000.0, "any_missing": False, "rows": []},
        "guardian": {"schema_version": "1.0", "agent_id": "repository_guardian", "repo_commit": repo_commit,
                     "run_timestamp": "t", "status": "completed", "guardian_status": "PASS", "findings": []},
        "supervisor": {"schema_version": "1.0", "agent_id": "project_supervisor", "repo_commit": repo_commit,
                       "run_timestamp": "t", "status": "completed", "overall_status": "OK", "findings": []},
        "evidence": {"schema_version": "1.0", "evidence_model_version": "1.0", "repo_commit": repo_commit,
                     "run_timestamp": "t", "freshness": "FRESH", "result": "OK",
                     "criterion_evidence": [{"criterion_id": "E1", "coverage_ratio": 1.0,
                                              "evidence_quality": "STRONG", "result": "OK",
                                              "missing_evidence": [], "supporting_sources": []}],
                     "contradictions": [], "missing_evidence": [],
                     "partner_readiness": [{"name": "FORITECH", "role": None, "wp_responsibility": None,
                                             "profile_status": "PROFILE_MISSING", "pic_status": "NOT_STARTED",
                                             "commitment_status": "NOT_STARTED", "personnel_status": "NOT_STARTED",
                                             "budget_status": "NOT_STARTED", "evidence_files": [],
                                             "missing_fields": [], "freshness": "UNAVAILABLE", "result": "FAIL"}],
                     "coverage_summary": {"overall_coverage_ratio": 1.0}},
        "proposal_intelligence": {"schema_version": "1.0", "agent_id": "proposal_intelligence",
                                   "repo_commit": repo_commit, "run_timestamp": "t", "status": "completed",
                                   "overall_status": "DIAGNOSTIC_COMPLETE", "fundability": "BORDERLINE",
                                   "diagnostic_score": {"total": 10.0}, "competitive_assessment": {"score": 3.0, "label": "STRONG"}},
    }
    if overrides:
        for key, patch in overrides.items():
            base[key].update(patch)
    for name, filename in cb.CONSUMED_STATE_FILES.items():
        (state_dir / filename).write_text(json.dumps(base[name]))


def _head(repo: Path) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True)
    return out.stdout.strip()


def _write_decision_log(repo: Path, extra: str = ""):
    (repo / "99_decisions").mkdir(exist_ok=True)
    text = (
        "# Decision Log\n\n"
        "## ISS-001 — First decision title\n\n"
        "**Date:** 2026-07-01\n"
        "**Status:** RESOLVED — done\n\n"
        "### Question\n\n"
        "Is X true?\n\n"
        "### Conclusion\n\n"
        "Yes, X is true because of Y.\n\n"
        "### Follow-up action\n\n"
        "None required.\n\n"
        + extra
    )
    (repo / "99_decisions" / "DECISION_LOG.md").write_text(text)


def _write_system_index(repo: Path):
    (repo / "00_baseline" / "FORITECH_SYSTEM_INDEX.md").write_text(
        "# System Index\n\n## Section A\n\nBody text.\n\n## Section B\n\nMore text.\n"
    )


# ── schema validation ────────────────────────────────────────────────────

def test_schema_is_valid_and_bundle_validates(fake_repo, state_dir, tmp_path):
    _write_all_state(state_dir, _head(fake_repo))
    _write_decision_log(fake_repo)
    _write_system_index(fake_repo)
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    jsonschema.validate(result, _schema())


def test_schema_rejects_missing_required_field():
    schema = _schema()
    bad = {"schema_version": "1.0"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_freshness_enum_enforced():
    schema = _schema()
    minimal = {k: None for k in schema["required"]}
    minimal["freshness"] = "NOT_A_REAL_VALUE"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(minimal, schema)


# ── deterministic generation ─────────────────────────────────────────────

def test_generation_is_deterministic_except_generation_id_and_timestamp(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    _write_decision_log(fake_repo)
    _write_system_index(fake_repo)

    r1 = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    r2 = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)

    for key in r1:
        if key in ("generation_id", "generated_at", "token_estimate"):
            continue
        assert r1[key] == r2[key], f"non-deterministic field: {key}"
    assert r1["generation_id"] != r2["generation_id"]


# ── atomic write ─────────────────────────────────────────────────────────

def test_atomic_write_leaves_no_temp_file(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    leftovers = list(state_dir.glob(f".{cb.STATE_FILENAME}.*.tmp"))
    assert leftovers == []
    assert (state_dir / cb.STATE_FILENAME).exists()


# ── proposal repo remains unchanged ──────────────────────────────────────

def test_proposal_repo_unmodified(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    _write_decision_log(fake_repo)
    _write_system_index(fake_repo)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                             capture_output=True, text=True).stdout
    cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                            capture_output=True, text=True).stdout
    assert before == after


# ── exact source paths ───────────────────────────────────────────────────

def test_every_canonical_source_has_exact_path(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert result["canonical_sources"][0]["path"] == "00_baseline/A.md"


def test_decision_has_exact_source_path(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    _write_decision_log(fake_repo)
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    all_decisions = result["open_decisions"] + result["recent_decisions"] + result["superseded_decisions"]
    assert any(d["source_path"] == "99_decisions/DECISION_LOG.md#ISS-001" for d in all_decisions)


def test_critical_finding_has_exact_source_path(fake_repo, state_dir):
    commit = _head(fake_repo)
    _write_all_state(state_dir, commit, overrides={
        "guardian": {"findings": [{"canonical_issue_key": "X.md", "severity": "critical", "title": "broken"}]}
    })
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert result["critical_findings"][0]["source_path"] == "guardian_state.json"


# ── missing state input ──────────────────────────────────────────────────

def test_missing_state_input_marked_unavailable_not_crashed(fake_repo, state_dir):
    # No state files written at all.
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    producers = {p["name"]: p for p in result["current_state"]["producers"]}
    assert all(p["available"] is False for p in producers.values())
    assert result["freshness"] in ("STALE", "UNAVAILABLE")


# ── invalid JSON input ────────────────────────────────────────────────────

def test_invalid_json_state_input_does_not_crash(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    (state_dir / "guardian_state.json").write_text("{not valid json")
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    producers = {p["name"]: p for p in result["current_state"]["producers"]}
    assert producers["guardian"]["available"] is False
    assert result["freshness"] == "STALE"


# ── stale repo commit ────────────────────────────────────────────────────

def test_stale_repo_commit_marks_bundle_stale(fake_repo, state_dir):
    _write_all_state(state_dir, "0000000")  # deliberately wrong commit
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert result["freshness"] == "STALE"


# ── mixed input commits ───────────────────────────────────────────────────

def test_mixed_commit_inputs_never_presented_as_fresh(fake_repo, state_dir):
    commit = _head(fake_repo)
    _write_all_state(state_dir, commit, overrides={"guardian": {"repo_commit": "deadbee"}})
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert result["freshness"] == "STALE"


# ── no generated guesses ─────────────────────────────────────────────────

def test_no_guesses_when_manifest_missing_project_fields(tmp_path, state_dir):
    repo = tmp_path / "repo2"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "canonical_documents.json").write_text(json.dumps({
        "manifest_version": "test", "current_phase": "baseline",
        "phases": {"baseline": {"depends_on": []}}, "documents": [],
    }))
    subprocess.run(["git", "init", "-q"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo)

    result = cb.run(repo, state_dir, service_repo_root=repo)
    assert result["project_id"] == "UNKNOWN"
    assert result["topic_id"] == "UNKNOWN"


def test_no_guesses_decision_fields_none_when_absent(fake_repo, state_dir):
    (fake_repo / "99_decisions").mkdir()
    (fake_repo / "99_decisions" / "DECISION_LOG.md").write_text(
        "## ISS-999 — Bare decision with no body\n\nSome unrelated text.\n"
    )
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    all_decisions = result["open_decisions"] + result["recent_decisions"] + result["superseded_decisions"]
    d = next(d for d in all_decisions if d["id"] == "ISS-999")
    assert d["date"] is None
    assert d["status"] is None
    assert d["question_summary"] is None
    assert d["conclusion_summary"] is None
    assert d["follow_up"] is None


# ── bootstrap token limit ────────────────────────────────────────────────

def test_bootstrap_token_estimate_within_target_range(fake_repo, state_dir):
    _write_all_state(state_dir, _head(fake_repo))
    _write_decision_log(fake_repo)
    _write_system_index(fake_repo)
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    tokens = result["token_estimate"]["estimated_tokens"]
    assert tokens < 20000  # sanity ceiling for this tiny fixture repo -- real-repo
    # verification (41 documents, 5 decisions) is checked separately, target 3000-6000.


# ── decision classification ───────────────────────────────────────────────

def test_superseded_status_classified_correctly(fake_repo, state_dir):
    _write_decision_log(fake_repo, extra=(
        "## ISS-002 — An old decision\n\n"
        "**Date:** 2026-01-01\n"
        "**Status:** SUPERSEDED by ISS-001\n\n"
        "### Question\n\nOld question.\n\n### Conclusion\n\nOld conclusion.\n"
    ))
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert any(d["id"] == "ISS-002" for d in result["superseded_decisions"])
    assert not any(d["id"] == "ISS-002" for d in result["recent_decisions"])


def test_open_status_classified_correctly(fake_repo, state_dir):
    _write_decision_log(fake_repo, extra=(
        "## ISS-003 — A still-open question\n\n"
        "**Date:** 2026-07-21\n"
        "**Status:** OPEN\n\n"
        "### Question\n\nUnresolved question.\n"
    ))
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    assert any(d["id"] == "ISS-003" for d in result["open_decisions"])


# ── commit classification never over-interprets ──────────────────────────

def test_completed_work_never_free_interprets_commit_message(fake_repo, state_dir):
    (fake_repo / "X.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=fake_repo)
    subprocess.run(["git", "commit", "-q", "-m", "this fixes everything forever"], cwd=fake_repo)
    result = cb.run(fake_repo, state_dir, service_repo_root=fake_repo)
    proposal_commits = [c for c in result["completed_work"] if c["repo"] == "proposal"]
    assert proposal_commits[0]["category"] in ("fix", "other")
    assert "subject" in proposal_commits[0]
    assert "everything forever" not in json.dumps(result.get("next_actions", []))
