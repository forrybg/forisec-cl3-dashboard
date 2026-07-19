"""
Additional Phase 2A hardening tests for app/state_reader.py: malformed
(non-dict) JSON must never crash the reader.
"""
import json

from app.state_reader import read_state


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
