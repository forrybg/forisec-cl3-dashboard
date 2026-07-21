"""
agents/project_supervisor.py

Written from scratch (NOT ported from the old system's Agent 0).
Watches only the FORISEC agent state files under FORISEC_STATE_DIR.
Never reads old canonical/ or old server/state/, never imports the
old supervisor.

Precedence rule (documented and tested):
    CRITICAL > DEGRADED > REVIEW > OK

CRITICAL  -- any guardian finding has severity == "critical".
DEGRADED  -- a required state file is missing, invalid JSON, or its
             repo_commit does not match the proposal repo's current HEAD.
REVIEW    -- there are REVIEW_REQUIRED documents, or the evaluator is
             active but scoring is not implemented, and no CRITICAL/
             DEGRADED condition applies.
OK        -- none of the above.

freshness/result split (STEP 1 OF 2, evidence-pipeline pass): each
watched file's health now carries a `status` (backward-compatible:
MISSING / INVALID / STALE / OK, unchanged), plus two independent new
fields:
  freshness -- FRESH / STALE / UNAVAILABLE (recency only)
  result    -- OK / REVIEW / WARN / FAIL / CRITICAL (content verdict only)
A file can be STALE and still report an OK result, or FRESH and still
report FAIL/CRITICAL -- the two axes are never conflated.

No circular dependency: this module does NOT read
proposal_intelligence_state.json or proposal_evidence_state.json --
the evidence pipeline reads *this* module's output, never the reverse.

Usage: python -m agents.project_supervisor
"""
from pathlib import Path

from agents.common import atomic_write_json, base_state, read_json_or_none

STATE_FILENAME = "supervisor_state.json"

WATCHED_FILES = ["docs_state.json", "evaluation_state.json", "guardian_state.json", "budget_state.json"]


def _result_for(filename: str, data: dict | None) -> str:
    """Content-only verdict for one producer's state, independent of
    freshness. Never invents a verdict for data it can't parse."""
    if data is None:
        return "FAIL"
    if filename == "guardian_state.json":
        findings = data.get("findings", [])
        if any(f.get("severity") == "critical" for f in findings):
            return "CRITICAL"
        return {"PASS": "OK", "WARN": "WARN", "FAIL": "FAIL"}.get(data.get("guardian_status"), "REVIEW")
    if filename == "docs_state.json":
        return {"ON_TRACK": "OK", "ON_TRACK_WITH_DRAFTS": "REVIEW", "WARN": "WARN", "FAIL": "FAIL"}.get(
            data.get("overall_status"), "REVIEW")
    if filename == "evaluation_state.json":
        return "OK" if data.get("status") == "completed" else "REVIEW"
    if filename == "budget_state.json":
        if not data.get("available"):
            return "FAIL"
        return "WARN" if data.get("any_missing") else "OK"
    return "REVIEW"


def _file_health(state_dir: Path, filename: str, live_commit: str | None) -> dict:
    path = state_dir / filename
    if not path.exists():
        return {"status": "MISSING", "freshness": "UNAVAILABLE", "result": "FAIL"}
    data = read_json_or_none(path)
    if data is None:
        return {"status": "INVALID", "freshness": "UNAVAILABLE", "result": "FAIL"}
    recorded_commit = data.get("repo_commit")
    result = _result_for(filename, data)
    if live_commit and recorded_commit and live_commit != recorded_commit:
        return {"status": "STALE", "repo_commit": recorded_commit, "freshness": "STALE", "result": result}
    freshness = "FRESH" if (live_commit and recorded_commit) else "UNAVAILABLE"
    return {"status": "OK", "repo_commit": recorded_commit, "freshness": freshness, "result": result}


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("project_supervisor", repo_root)
    live_commit = base["repo_commit"]

    file_health = {fn: _file_health(state_dir, fn, live_commit) for fn in WATCHED_FILES}

    docs_state = read_json_or_none(state_dir / "docs_state.json")
    eval_state = read_json_or_none(state_dir / "evaluation_state.json")
    guardian_state = read_json_or_none(state_dir / "guardian_state.json")

    findings = []

    any_missing_or_invalid = any(h["status"] in ("MISSING", "INVALID") for h in file_health.values())
    any_stale = any(h["status"] == "STALE" for h in file_health.values())

    guardian_findings = (guardian_state or {}).get("findings", [])
    critical_findings = [f for f in guardian_findings if f.get("severity") == "critical"]

    pending_reviews = 0
    if docs_state:
        pending_reviews = sum(
            1 for d in docs_state.get("documents", [])
            if d.get("status") in ("REVIEW_REQUIRED", "EVIDENCE_REQUIRED")
        )

    evaluator_active_unscored = bool(
        eval_state
        and eval_state.get("mode") is not None
        and eval_state.get("score") is None
    )

    # Precedence: CRITICAL > DEGRADED > REVIEW > OK.
    if critical_findings:
        overall_status = "CRITICAL"
        findings.append({"severity": "critical",
                          "title": f"{len(critical_findings)} critical guardian finding(s)"})
    elif any_missing_or_invalid or any_stale:
        overall_status = "DEGRADED"
        for fn, h in file_health.items():
            if h["status"] in ("MISSING", "INVALID", "STALE"):
                findings.append({"severity": "warning", "title": f"{fn}: {h['status']}"})
    elif pending_reviews > 0 or evaluator_active_unscored:
        overall_status = "REVIEW"
        if pending_reviews:
            findings.append({"severity": "info", "title": f"{pending_reviews} document(s) REVIEW_REQUIRED"})
        if evaluator_active_unscored:
            findings.append({"severity": "info", "title": "Evaluator active, scoring not implemented"})
    else:
        overall_status = "OK"

    result = {
        **base,
        "status": "completed",
        "overall_status": overall_status,
        "state_files": file_health,
        "critical_finding_count": len(critical_findings),
        "pending_review_count": pending_reviews,
        "evaluator_active_unscored": evaluator_active_unscored,
        "findings": findings,
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "project_supervisor")


if __name__ == "__main__":
    main()
