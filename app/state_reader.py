"""
app/state_reader.py

Read-only access to the agent state JSON files under
FORISEC_STATE_DIR. The dashboard NEVER scans markdown itself, never
runs agents, never writes state -- it only reads what the agents
already produced. The only "live" operation permitted is a safe,
read-only `git rev-parse HEAD` against FORISEC_REPO_ROOT, used purely
to compute freshness.
"""
import json
import subprocess
from pathlib import Path

STATE_FILES = {
    "docs": "docs_state.json",
    "evaluation": "evaluation_state.json",
    "guardian": "guardian_state.json",
    "supervisor": "supervisor_state.json",
    "proposal_intelligence": "proposal_intelligence_state.json",
    "decisions": "decisions_state.json",
    "budget": "budget_state.json",
    "evidence": "proposal_evidence_state.json",
    "services": "services_status.json",
}


def get_live_repo_commit(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def read_state(state_dir: Path, key: str, repo_root: Path) -> dict:
    filename = STATE_FILES[key]
    path = state_dir / filename
    if not path.exists():
        return {"available": False, "status": "AGENT_UNAVAILABLE",
                "reason": "No run has been recorded yet for this agent."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"available": False, "status": "AGENT_UNAVAILABLE",
                "reason": f"State file is invalid JSON: {e}"}

    if not isinstance(data, dict):
        return {"available": False, "status": "AGENT_UNAVAILABLE",
                "reason": f"State file does not contain a JSON object "
                          f"(got {type(data).__name__})."}

    data["available"] = True
    live_commit = get_live_repo_commit(repo_root)
    recorded_commit = data.get("repo_commit")
    if live_commit and recorded_commit:
        data["freshness"] = "FRESH" if live_commit == recorded_commit else "STALE"
        data["live_repo_commit"] = live_commit
    else:
        data["freshness"] = "UNKNOWN"
    return data


def read_all_state(state_dir: Path, repo_root: Path) -> dict:
    return {key: read_state(state_dir, key, repo_root) for key in STATE_FILES}


def _classify_event_type(prev_record: dict | None, record: dict) -> str:
    """
    Mirrors agents/proposal_intelligence.py::_classify_event_type exactly
    (duplicated, not imported -- app/ must never import agents/, per
    tests/test_isolation.py::test_dashboard_does_not_import_agents).

    Needed here because this module's read_history() is a SEPARATE read
    path from the one agents/proposal_intelligence.py uses internally to
    build timeline_summary: most snapshot files on disk predate the
    event_type field and were only ever backfilled in-memory by the
    agent's own _read_timeline(), never persisted to the JSON files. Without
    doing the same backfill here, the dashboard's live chart (fed by this
    function via /api/v1/proposal-intelligence/history) sees almost no
    REPOSITORY_CHANGE-tagged records even though timeline_summary (baked
    into proposal_intelligence_state.json) correctly counts 18 of them --
    that mismatch was the "18/1 repository changes but not enough for a
    trend chart" bug.
    """
    if prev_record is None:
        return "REPOSITORY_CHANGE"
    if record.get("promotion_status") == "APPROVED" and prev_record.get("promotion_status") != "APPROVED":
        return "HUMAN_PROMOTION"
    if prev_record.get("repo_commit") != record.get("repo_commit"):
        return "REPOSITORY_CHANGE"
    if prev_record.get("scoring_model_version") != record.get("scoring_model_version"):
        return "MODEL_RECALCULATION"
    return "REPOSITORY_CHANGE"


def read_history(state_dir: Path) -> list[dict]:
    """Read-only: compact evaluation-timeline snapshots written by Agent 5
    (agents/proposal_intelligence.py) under FORISEC_STATE_DIR/history/.
    Never written to by the dashboard."""
    history_dir = state_dir / "history"
    if not history_dir.exists():
        return []
    records = []
    for f in sorted(history_dir.glob("evaluation_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                records.append(data)
        except Exception:
            continue
    records.sort(key=lambda r: r.get("timestamp", ""))
    # Backfill event_type for snapshots written before this field existed --
    # read-only classification, never rewrites the snapshot file on disk.
    prev = None
    for r in records:
        if "event_type" not in r:
            r["event_type"] = _classify_event_type(prev, r)
        prev = r
    return records
