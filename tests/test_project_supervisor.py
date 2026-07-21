import json

from agents import project_supervisor


def _write(state_dir, filename, data):
    (state_dir / filename).write_text(json.dumps(data))


def test_supervisor_precedence_critical_over_everything(fake_repo, state_dir):
    _write(state_dir, "guardian_state.json", {
        "repo_commit": "abc", "findings": [{"severity": "critical", "title": "x"}]
    })
    _write(state_dir, "docs_state.json", {"repo_commit": "abc", "documents": []})
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "CRITICAL"


def test_supervisor_degraded_on_missing_file(fake_repo, state_dir):
    # no state files written at all
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "DEGRADED"


def test_supervisor_degraded_on_stale_commit(fake_repo, state_dir):
    _write(state_dir, "guardian_state.json", {"repo_commit": "deadbeef", "findings": []})
    _write(state_dir, "docs_state.json", {"repo_commit": "deadbeef", "documents": []})
    _write(state_dir, "evaluation_state.json", {"repo_commit": "deadbeef", "mode": None, "score": None})
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "DEGRADED"


def test_supervisor_review_when_pending_reviews(fake_repo, state_dir):
    from agents.common import get_repo_commit
    commit = get_repo_commit(fake_repo)
    _write(state_dir, "guardian_state.json", {"repo_commit": commit, "findings": []})
    _write(state_dir, "docs_state.json", {"repo_commit": commit,
           "documents": [{"path": "x", "status": "REVIEW_REQUIRED"}]})
    _write(state_dir, "evaluation_state.json", {"repo_commit": commit, "mode": None, "score": None})
    _write(state_dir, "budget_state.json", {"repo_commit": commit, "available": True, "any_missing": False})
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "REVIEW"


def test_supervisor_ok_when_clean(fake_repo, state_dir):
    from agents.common import get_repo_commit
    commit = get_repo_commit(fake_repo)
    _write(state_dir, "guardian_state.json", {"repo_commit": commit, "findings": []})
    _write(state_dir, "docs_state.json", {"repo_commit": commit, "documents": []})
    _write(state_dir, "evaluation_state.json", {"repo_commit": commit, "mode": None, "score": None})
    _write(state_dir, "budget_state.json", {"repo_commit": commit, "available": True, "any_missing": False})
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "OK"


def test_supervisor_freshness_result_independent(fake_repo, state_dir):
    """freshness and result must never be conflated: a STALE file can
    still report a non-FAIL result, computed purely from its content."""
    _write(state_dir, "guardian_state.json", {"repo_commit": "deadbeef", "guardian_status": "PASS", "findings": []})
    _write(state_dir, "docs_state.json", {"repo_commit": "deadbeef", "overall_status": "ON_TRACK", "documents": []})
    _write(state_dir, "evaluation_state.json", {"repo_commit": "deadbeef", "status": "completed", "mode": None, "score": None})
    _write(state_dir, "budget_state.json", {"repo_commit": "deadbeef", "available": True, "any_missing": False})
    result = project_supervisor.run(fake_repo, state_dir)
    guardian_health = result["state_files"]["guardian_state.json"]
    assert guardian_health["freshness"] == "STALE"
    assert guardian_health["result"] == "OK"  # content is fine even though the snapshot is stale


def test_supervisor_no_circular_dependency_on_evidence_or_intelligence():
    """Supervisor must never read Agent 5's or the evidence pipeline's
    own output -- the evidence pipeline reads supervisor_state.json,
    never the other way round. Checked against the actual watched-file
    list (not a docstring-sensitive substring scan)."""
    assert "proposal_intelligence_state.json" not in project_supervisor.WATCHED_FILES
    assert "proposal_evidence_state.json" not in project_supervisor.WATCHED_FILES
