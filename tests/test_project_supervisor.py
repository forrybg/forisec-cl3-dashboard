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
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "REVIEW"


def test_supervisor_ok_when_clean(fake_repo, state_dir):
    from agents.common import get_repo_commit
    commit = get_repo_commit(fake_repo)
    _write(state_dir, "guardian_state.json", {"repo_commit": commit, "findings": []})
    _write(state_dir, "docs_state.json", {"repo_commit": commit, "documents": []})
    _write(state_dir, "evaluation_state.json", {"repo_commit": commit, "mode": None, "score": None})
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "OK"
