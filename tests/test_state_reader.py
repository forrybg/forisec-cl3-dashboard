"""
Additional Phase 2A hardening tests for app/state_reader.py: malformed
(non-dict) JSON must never crash the reader.
"""
import json

from app.state_reader import read_state, read_history


def test_non_dict_json_reports_agent_unavailable(fake_repo, state_dir):
    (state_dir / "docs_state.json").write_text(json.dumps([1, 2, 3]))
    result = read_state(state_dir, "docs", fake_repo)
    assert result["available"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"


def test_scalar_json_reports_agent_unavailable(fake_repo, state_dir):
    (state_dir / "guardian_state.json").write_text(json.dumps("just a string"))
    result = read_state(state_dir, "guardian", fake_repo)
    assert result["available"] is False
    assert result["status"] == "AGENT_UNAVAILABLE"


def test_malformed_json_never_raises(fake_repo, state_dir):
    (state_dir / "evaluation_state.json").write_text("{not: valid json,,,")
    # Must not raise -- caller (dashboard route) gets a controlled dict back.
    result = read_state(state_dir, "evaluation", fake_repo)
    assert result["status"] == "AGENT_UNAVAILABLE"


# ── event_type backfill (regression: dashboard chart showed "not enough
#    REPOSITORY_CHANGE snapshots" while timeline_summary correctly counted
#    18, because this read path never backfilled event_type like
#    agents/proposal_intelligence.py's own _read_timeline() does) ─────────

def test_read_history_backfills_missing_event_type(state_dir):
    history_dir = state_dir / "history"
    history_dir.mkdir()
    # Simulates pre-STEP-1 snapshots that predate the event_type field.
    (history_dir / "evaluation_2026-01-01T00-00-00_commitA.json").write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "commitA",
        "scoring_model_version": "1.1", "total": 14.5, "promotion_status": "BLOCKED",
    }))
    (history_dir / "evaluation_2026-01-02T00-00-00_commitB.json").write_text(json.dumps({
        "timestamp": "2026-01-02T00:00:00", "repo_commit": "commitB",
        "scoring_model_version": "1.1", "total": 12.0, "promotion_status": "PENDING_REVIEW",
    }))
    records = read_history(state_dir)
    assert len(records) == 2
    assert all("event_type" in r for r in records)
    assert records[0]["event_type"] == "REPOSITORY_CHANGE"  # first-ever snapshot
    assert records[1]["event_type"] == "REPOSITORY_CHANGE"  # commit changed


def test_read_history_backfill_classifies_model_recalculation(state_dir):
    history_dir = state_dir / "history"
    history_dir.mkdir()
    (history_dir / "evaluation_2026-01-01T00-00-00_commitA.json").write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "commitA",
        "scoring_model_version": "1.1", "total": 14.5, "promotion_status": "BLOCKED",
    }))
    (history_dir / "evaluation_2026-01-02T00-00-00_commitA.json").write_text(json.dumps({
        "timestamp": "2026-01-02T00:00:00", "repo_commit": "commitA",
        "scoring_model_version": "2.0", "total": 6.5, "promotion_status": "BLOCKED",
    }))
    records = read_history(state_dir)
    assert records[0]["event_type"] == "REPOSITORY_CHANGE"
    assert records[1]["event_type"] == "MODEL_RECALCULATION"


def test_read_history_never_overwrites_existing_event_type(state_dir):
    history_dir = state_dir / "history"
    history_dir.mkdir()
    (history_dir / "evaluation_2026-01-01T00-00-00_commitA.json").write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "commitA",
        "scoring_model_version": "2.0", "total": 6.5, "promotion_status": "BLOCKED",
        "event_type": "HUMAN_PROMOTION",  # already tagged -- must be preserved verbatim
    }))
    records = read_history(state_dir)
    assert records[0]["event_type"] == "HUMAN_PROMOTION"


def test_read_history_never_writes_to_disk(state_dir):
    history_dir = state_dir / "history"
    history_dir.mkdir()
    f = history_dir / "evaluation_2026-01-01T00-00-00_commitA.json"
    f.write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "commitA",
        "scoring_model_version": "1.1", "total": 14.5, "promotion_status": "BLOCKED",
    }))
    before = f.read_text()
    read_history(state_dir)
    after = f.read_text()
    assert before == after  # backfill is in-memory only, never rewrites the snapshot file
