"""
app/state_reader.py

Read-only access to the four agent state JSON files under
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
