"""
agents/decision_log.py

Deterministic, read-only parser for 99_decisions/DECISION_LOG.md.
Extracts each "## ISS-NNN -- Title" entry with its Date/Status fields
into a structured state file (decisions_state.json), following the
same pattern as repository_guardian.py: run at refresh time via
scripts/refresh_agents.sh, never at request time (see app/main.py
docstring -- the dashboard only ever reads JSON from FORISEC_STATE_DIR).

Usage: python -m agents.decision_log
"""
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state, safe_repo_path

STATE_FILENAME = "decisions_state.json"
LOG_RELATIVE_PATH = "99_decisions/DECISION_LOG.md"

# Matches "## ISS-001 -- Title text" (em dash or double hyphen accepted)
ISS_HEADER_RE = re.compile(r"^##\s+(ISS-\d+)\s*[-\u2014]\s*(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"^\*\*Date:\*\*\s*(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def parse_decision_log(text: str) -> list[dict]:
    headers = list(ISS_HEADER_RE.finditer(text))
    entries = []
    for i, m in enumerate(headers):
        iss_id, title = m.group(1), m.group(2).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]

        date_m = DATE_RE.search(block)
        status_m = STATUS_RE.search(block)
        status_raw = status_m.group(1).strip() if status_m else "UNKNOWN"
        # Status lines look like "RESOLVED -- present, under renamed/merged task"
        status_word = re.split(r"\s*[-\u2014]\s*", status_raw, maxsplit=1)[0].strip()

        entries.append({
            "id": iss_id,
            "title": title,
            "date": date_m.group(1).strip() if date_m else None,
            "status": status_word,
            "status_detail": status_raw,
        })
    return entries


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("decision_log", repo_root)

    try:
        log_path = safe_repo_path(repo_root, LOG_RELATIVE_PATH)
    except Exception as e:
        result = {
            **base, "status": "completed", "available": False,
            "reason": f"Unsafe or missing path: {e}",
            "entries": [], "resolved_count": 0, "open_count": 0,
        }
        atomic_write_json(state_dir / STATE_FILENAME, result)
        return result

    if not log_path.exists():
        result = {
            **base, "status": "completed", "available": False,
            "reason": f"{LOG_RELATIVE_PATH} does not exist under repo root.",
            "entries": [], "resolved_count": 0, "open_count": 0,
        }
        atomic_write_json(state_dir / STATE_FILENAME, result)
        return result

    text = log_path.read_text(encoding="utf-8")
    entries = parse_decision_log(text)
    resolved = [e for e in entries if e["status"].upper() == "RESOLVED"]
    open_entries = [e for e in entries if e["status"].upper() not in ("RESOLVED", "CLOSED")]

    result = {
        **base,
        "status": "completed",
        "available": True,
        "source": LOG_RELATIVE_PATH,
        "entries": entries,
        "resolved_count": len(resolved),
        "open_count": len(open_entries),
        "summary": f"{len(entries)} ISS entr{'y' if len(entries)==1 else 'ies'} "
                   f"({len(resolved)} resolved, {len(open_entries)} open).",
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "decision_log")


if __name__ == "__main__":
    main()
