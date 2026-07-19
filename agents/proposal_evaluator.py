"""
agents/proposal_evaluator.py

Ported (not copied) from the old system's evaluator agent, used only
as read-only reference. Consumes docs_controller's own output (never
re-derives file-existence itself). Activation gate only -- score stays
null; real Excellence/Impact/Implementation scoring is explicitly out
of scope (no fabricated score is ever implemented).

Usage: python -m agents.proposal_evaluator
"""
from pathlib import Path

from agents.common import atomic_write_json, base_state, read_json_or_none

STATE_FILENAME = "evaluation_state.json"
DOCS_STATE_FILENAME = "docs_state.json"

REQUIRED_PROPOSAL_DOCS = [
    "04_proposal/EXCELLENCE.md",
    "04_proposal/IMPACT.md",
    "04_proposal/IMPLEMENTATION.md",
]

ACTIVATING_STATUSES = {"DRAFT", "FROZEN", "REVIEW_REQUIRED", "EVIDENCE_REQUIRED"}


def check_activation(docs_state: dict | None) -> dict:
    if docs_state is None:
        return {"activated": False, "mode": None, "statuses": {},
                "reason": "docs_controller has not run yet -- cannot determine "
                          "proposal-section status."}

    by_path = {d["path"]: d["status"] for d in docs_state.get("documents", [])}
    statuses = {p: by_path.get(p, "NOT_STARTED") for p in REQUIRED_PROPOSAL_DOCS}
    missing = [p for p, s in statuses.items() if s not in ACTIVATING_STATUSES]
    if missing:
        return {"activated": False, "mode": None, "statuses": statuses,
                "reason": "No accepted proposal narrative exists yet for the "
                          "Excellence, Impact and Implementation evaluation criteria.",
                "missing": missing}

    all_frozen = all(s == "FROZEN" for s in statuses.values())
    return {"activated": True, "mode": "evaluation" if all_frozen else "diagnostic",
            "statuses": statuses, "reason": None}


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("proposal_evaluator", repo_root)
    docs_state = read_json_or_none(state_dir / DOCS_STATE_FILENAME)
    activation = check_activation(docs_state)

    result = {
        **base,
        "status": "completed",
        "target_repo": str(repo_root),
        "required_inputs": REQUIRED_PROPOSAL_DOCS,
        "proposal_section_status": activation["statuses"],
    }

    if not activation["activated"]:
        result.update({
            "overall_status": "NOT_APPLICABLE_YET",
            "mode": None,
            "reason": activation["reason"],
            "next_action": "Complete the WP1-WP6 detailed descriptions and create the "
                            "first proposal narrative (04_proposal/EXCELLENCE.md, IMPACT.md, "
                            "IMPLEMENTATION.md).",
            "score": None,
            "findings": [],
            "warnings": [],
            "errors": [],
        })
        atomic_write_json(state_dir / STATE_FILENAME, result)
        return result

    # Activated: mode selected, but scoring is deliberately NOT
    # implemented. score MUST stay null -- never 0, never a fabricated
    # number -- and the mandatory finding below must always be present.
    result.update({
        "overall_status": f"ACTIVE_{activation['mode'].upper()}_MODE",
        "mode": activation["mode"],
        "reason": None,
        "next_action": "Activation gate has fired correctly; real Excellence/Impact/"
                        "Implementation scoring is not implemented in this service.",
        "score": None,
        "findings": [{
            "id": "scoring-not-yet-implemented",
            "severity": "info",
            "title": "Activation gate fired, scoring logic pending",
            "description": f"All three proposal sections are at least DRAFT "
                            f"(mode={activation['mode']}), but the scoring rubric "
                            f"has not been implemented.",
            "source": "04_proposal/",
        }],
        "warnings": [],
        "errors": [],
    })
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "proposal_evaluator")


if __name__ == "__main__":
    main()
