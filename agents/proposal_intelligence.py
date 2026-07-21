"""
agents/proposal_intelligence.py — Agent 5: Proposal Intelligence.

Fully independent of the old system's Agent 5: no import, no shared
state, no reading of old snapshots, no runtime dependency on it. This
module is deterministic and rule-based -- there is no hidden LLM call
anywhere in the scoring path. Every positive point requires a matching
evidence entry (a keyword/structural signal actually found in the
proposal text); every missing signal is recorded as missing_evidence
and never silently treated as a negative claim about quality.

This produces a DIAGNOSTIC score only -- never an official EC
evaluation, and never auto-promoted to a canonical/approved score.

Usage: python -m agents.proposal_intelligence
"""
import json
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state, read_json_or_none, now_iso

STATE_FILENAME = "proposal_intelligence_state.json"
SCORING_MODEL_VERSION = "1.1"  # 1.1: fixed E1 word-count signal not contributing to score

# ── Section signal rubric ────────────────────────────────────────────────
# Each criterion is scored out of 5.0 in 1.0-point increments (one point
# per matched signal), rounded to the nearest 0.5 by construction (all
# increments are 1.0, so no rounding is actually needed -- kept simple
# and auditable rather than clever).
CRITERIA = {
    "E1": {
        "title": "Objectives and Ambition",
        "file": "04_proposal/EXCELLENCE.md",
        "signals": [
            (r"\bobjective", "keyword_presence", "Mentions project objectives"),
            (r"state[- ]of[- ]the[- ]art", "keyword_presence", "References state-of-the-art comparison"),
            (r"\bTRL\s*\d", "keyword_presence", "Cites a Technology Readiness Level"),
            (r"\d+(\.\d+)?\s*%|\d+\s*(ms|verifications?/sec|milliseconds)", "quantified_claim", "Contains a quantified performance/impact figure"),
        ],
        "min_word_count": 600,
    },
    "E2": {
        "title": "Methodology",
        "file": "04_proposal/EXCELLENCE.md",
        "signals": [
            (r"\bmethodolog", "keyword_presence", "Describes methodology"),
            (r"\brisk\b.*\bmitigat", "keyword_presence", "Discusses risk mitigation"),
            (r"\bwork\s*package|\bWP\d", "keyword_presence", "References work packages"),
            (r"\bvalidat(e|ion)", "keyword_presence", "Discusses validation approach"),
            (r"\bformal\b.*\b(verification|analysis)", "keyword_presence", "References formal verification/analysis"),
        ],
        "min_word_count": 600,
    },
    "I1": {
        "title": "Pathways to Outcomes",
        "file": "04_proposal/IMPACT.md",
        "signals": [
            (r"\boutcome", "keyword_presence", "Discusses outcomes"),
            (r"impact\s*pathway|theory\s*of\s*change", "keyword_presence", "Describes an impact pathway"),
            (r"\bKPI|key\s*performance\s*indicator", "keyword_presence", "Defines KPIs"),
            (r"target\s*group|stakeholder", "keyword_presence", "Identifies target groups/stakeholders"),
            (r"measurable|quantifi", "keyword_presence", "Uses measurable/quantified language"),
        ],
        "min_word_count": 600,
    },
    "I2I3": {
        "title": "Dissemination, Exploitation and EU Value",
        "file": "04_proposal/IMPACT.md",
        "signals": [
            (r"\bdissemination", "keyword_presence", "Discusses dissemination"),
            (r"\bexploitation", "keyword_presence", "Discusses exploitation"),
            (r"open\s*science|open\s*access", "keyword_presence", "References open science/access"),
            (r"european\s*(digital\s*)?sovereignty|strategic\s*autonomy|EU\s*value", "keyword_presence", "Addresses EU strategic value"),
            (r"standard(isation|ization)|\bETSI\b|\bIETF\b", "keyword_presence", "References standardisation activity"),
        ],
        "min_word_count": 600,
    },
    "IM1": {
        "title": "Work Plan",
        "file": "04_proposal/IMPLEMENTATION.md",
        "signals": [
            (r"work\s*plan", "keyword_presence", "Describes the work plan"),
            (r"\bGantt\b|\bPERT\b|\bmilestone", "keyword_presence", "References Gantt/PERT/milestones"),
            (r"\bdeliverable", "keyword_presence", "References deliverables"),
            (r"risk\s*register|\bR\d+\b\s*[-\u2014]", "keyword_presence", "References a risk register"),
            (r"dependenc", "keyword_presence", "Discusses dependencies"),
        ],
        "min_word_count": 600,
    },
    "IM2IM3": {
        "title": "Consortium and Resources",
        "file": "04_proposal/IMPLEMENTATION.md",
        "signals": [
            (r"\bconsortium", "keyword_presence", "Describes the consortium"),
            (r"person[- ]month|\bPM\b", "keyword_presence", "References person-months"),
            (r"\bbudget", "keyword_presence", "References budget"),
            (r"\bpartner", "keyword_presence", "References partners"),
            (r"equipment|subcontract", "keyword_presence", "References equipment/subcontracting"),
        ],
        "min_word_count": 600,
    },
}

MAX_SCORE_PER_CRITERION = 5.0


def _read_proposal_file(repo_root: Path, rel_path: str) -> str | None:
    full = repo_root / rel_path
    if not full.exists():
        return None
    try:
        text = full.read_text(encoding="utf-8")
    except Exception:
        return None
    return text if text.strip() else None


def _score_criterion(criterion_id: str, spec: dict, text: str | None,
                      doc_status: str | None, guardian_has_critical: bool) -> dict:
    evidence = []
    missing_evidence = []
    file = spec["file"]

    if text is None:
        return {
            "criterion_id": criterion_id,
            "title": spec["title"],
            "score": 0.0,
            "max_score": MAX_SCORE_PER_CRITERION,
            "confidence": 0.1,
            "summary": f"{file} does not exist or is empty -- no evidence available.",
            "strengths": [],
            "weaknesses": [f"{file} is missing or empty."],
            "red_flags": [],
            "critical_fixes": [f"Create {file} with content addressing {spec['title']}."],
            "evidence": [],
            "missing_evidence": [s[2] for s in spec["signals"]] + ["document exists"],
        }

    signal_points = 0.0
    for pattern, evidence_type, description in spec["signals"]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            excerpt = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ").strip()
            evidence.append({
                "file": file, "section": None, "basis": description,
                "evidence_type": evidence_type, "excerpt": excerpt[:160],
            })
            signal_points += 1.0
        else:
            missing_evidence.append(description)

    word_count = len(text.split())
    if word_count >= spec["min_word_count"]:
        evidence.append({
            "file": file, "section": None,
            "basis": f"Section reaches {word_count} words (>= {spec['min_word_count']})",
            "evidence_type": "structural_completeness", "excerpt": None,
        })
        signal_points += 1.0
    else:
        missing_evidence.append(f"word count below {spec['min_word_count']} (actual: {word_count})")

    raw_score = min(signal_points, MAX_SCORE_PER_CRITERION)
    score = round(raw_score * 2) / 2  # nearest 0.5

    confidence = 1.0
    confidence -= 0.1 * len(missing_evidence)
    if doc_status and doc_status != "FROZEN":
        confidence -= 0.15
    if guardian_has_critical:
        confidence -= 0.2
    confidence = round(max(0.1, min(1.0, confidence)), 2)

    strengths = [e["basis"] for e in evidence]
    weaknesses = [f"Missing: {m}" for m in missing_evidence]
    red_flags = []
    if score == 0.0:
        red_flags.append(f"No supporting evidence found in {file} for {spec['title']}.")
    critical_fixes = []
    if missing_evidence:
        critical_fixes.append(
            f"Add explicit content in {file} addressing: {', '.join(missing_evidence)}."
        )

    return {
        "criterion_id": criterion_id,
        "title": spec["title"],
        "score": score,
        "max_score": MAX_SCORE_PER_CRITERION,
        "confidence": confidence,
        "summary": f"{len(evidence)} evidence signal(s) found, {len(missing_evidence)} missing, in {file}.",
        "strengths": strengths,
        "weaknesses": weaknesses,
        "red_flags": red_flags,
        "critical_fixes": critical_fixes,
        "evidence": evidence,
        "missing_evidence": missing_evidence,
    }


def _competitive_assessment(section_scores: list[dict], docs_state: dict | None,
                             guardian_state: dict | None) -> dict:
    """
    Deterministic 0-1 components, documented and testable -- no hidden
    LLM values anywhere in this formula.
    """
    confidences = [s["confidence"] for s in section_scores]
    confidence_factor = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

    partner_docs = []
    if docs_state:
        partner_docs = [d for d in docs_state.get("documents", []) if d.get("path", "").startswith("05_partners/")]
    if partner_docs:
        frozen = sum(1 for d in partner_docs if d.get("status") == "FROZEN")
        consortium_credibility = round(frozen / len(partner_docs), 3)
    else:
        consortium_credibility = 0.0

    guardian_findings = (guardian_state or {}).get("findings", [])
    critical_count = sum(1 for f in guardian_findings if f.get("severity") == "critical")
    high_count = sum(1 for f in guardian_findings if f.get("severity") == "high")
    risk_perception = round(max(0.0, 1.0 - min(1.0, critical_count * 0.4 + high_count * 0.15)), 3)

    strong_criteria = sum(1 for s in section_scores if s["score"] >= 3.0)
    strategic_fit = round(strong_criteria / len(section_scores), 3) if section_scores else 0.0

    all_evidence_bases = " ".join(
        e["basis"] for s in section_scores for e in s["evidence"]
    ).lower()
    differentiation_keywords = ["post-quantum", "verification", "trust", "provenance", "ftech"]
    hits = sum(1 for kw in differentiation_keywords if kw in all_evidence_bases)
    differentiation = round(hits / len(differentiation_keywords), 3)

    components = {
        "confidence_factor": {"score": confidence_factor, "rationale": "Average confidence across the six diagnostic criteria.", "evidence": [], "blockers": []},
        "consortium_credibility": {"score": consortium_credibility, "rationale": "Fraction of 05_partners/*.md profiles at FROZEN status.", "evidence": [], "blockers": [] if partner_docs else ["No partner profile documents found."]},
        "risk_perception": {"score": risk_perception, "rationale": "Derived from Guardian critical/high finding counts (fewer findings = lower perceived risk).", "evidence": [], "blockers": [f"{critical_count} critical, {high_count} high Guardian finding(s)."] if (critical_count or high_count) else []},
        "strategic_fit": {"score": strategic_fit, "rationale": "Fraction of the six criteria scoring >= 3.0/5.", "evidence": [], "blockers": []},
        "differentiation": {"score": differentiation, "rationale": "Fraction of known differentiation keywords found across collected evidence.", "evidence": [], "blockers": []},
    }

    avg = sum(c["score"] for c in components.values()) / len(components)
    competitive_score = round(avg * 5, 2)

    if competitive_score < 1.5:
        label = "NOT COMPETITIVE"
    elif competitive_score < 2.5:
        label = "WEAK"
    elif competitive_score < 3.5:
        label = "COMPETITIVE BUT WEAK"
    elif competitive_score < 4.25:
        label = "STRONG"
    else:
        label = "TOP TIER"

    return {"score": competitive_score, "label": label, "components": components}


def _build_improvement_loop(section_scores: list[dict]) -> tuple[list, list, list]:
    weaknesses, evidence_packs, fix_packs = [], [], []
    for s in section_scores:
        if s["score"] >= 3.5 and not s["missing_evidence"]:
            continue
        severity = "high" if s["score"] < 2.0 else "medium"
        weakness_id = f"weakness-{s['criterion_id']}"
        weaknesses.append({
            "id": weakness_id, "criterion": s["criterion_id"], "severity": severity,
            "title": f"{s['title']} under-evidenced", "description": s["summary"],
            "evidence": s["evidence"], "confidence": s["confidence"], "status": "OPEN",
        })
        evidence_packs.append({
            "weakness_id": weakness_id,
            "source_documents": [CRITERIA[s["criterion_id"]]["file"]],
            "best_evidence": s["evidence"],
            "evidence_score": s["confidence"],
            "gaps": s["missing_evidence"],
        })
        fix_packs.append({
            "id": f"fixpack-{s['criterion_id']}",
            "weakness_id": weakness_id,
            "criterion": s["criterion_id"],
            "proposed_action": s["critical_fixes"][0] if s["critical_fixes"] else "Add supporting evidence.",
            "affected_files": [CRITERIA[s["criterion_id"]]["file"]],
            "required_evidence": s["missing_evidence"],
            "status": "PENDING_REVIEW",
            "human_approval_required": True,
        })
    return weaknesses, evidence_packs, fix_packs


def _fundability_label(total: float, has_critical: bool, promotion_status: str) -> str:
    if has_critical or promotion_status == "BLOCKED":
        return "BLOCKED"
    if total < 10:
        return "NOT READY"
    if total < 12:
        return "BORDERLINE"
    if total < 13.5:
        return "COMPETITIVE"
    return "STRONG"


EVENT_TYPES = ("REPOSITORY_CHANGE", "MODEL_RECALCULATION", "HUMAN_PROMOTION")


def _classify_event_type(prev_record: dict | None, record: dict) -> str:
    """
    Timeline event classification (STEP 1 OF 2, evidence-pipeline pass).
    Does NOT change the scoring formula or total_gain calculation --
    purely descriptive metadata on top of the existing snapshot shape.

    REPOSITORY_CHANGE    -- repo_commit differs from the previous snapshot
                             (or this is the first-ever snapshot).
    MODEL_RECALCULATION  -- same repo_commit, but scoring_model_version
                             changed (e.g. a rubric bugfix, not a proposal edit).
    HUMAN_PROMOTION      -- promotion_status newly became APPROVED (never
                             set by this agent itself, only observed here).
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


def _write_history_snapshot(state_dir: Path, repo_commit: str | None, snapshot: dict, force: bool = False) -> bool:
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    commit_key = repo_commit or "unknown"
    existing = sorted(history_dir.glob(f"evaluation_*_{commit_key}.json"))
    if existing and not force:
        try:
            latest = json.loads(existing[-1].read_text(encoding="utf-8"))
            if latest.get("scoring_model_version") == SCORING_MODEL_VERSION:
                return False
        except Exception:
            pass
    ts_safe = now_iso().replace(":", "-")
    fname = f"evaluation_{ts_safe}_{commit_key}.json"
    atomic_write_json(history_dir / fname, snapshot)
    return True


def _read_timeline(state_dir: Path) -> list[dict]:
    history_dir = state_dir / "history"
    if not history_dir.exists():
        return []
    records = []
    for f in sorted(history_dir.glob("evaluation_*.json")):
        data = read_json_or_none(f)
        if data:
            records.append(data)
    records.sort(key=lambda r: r.get("timestamp", ""))
    # Backfill event_type for snapshots written before this field existed --
    # read-only classification, never rewrites the snapshot file on disk.
    prev = None
    for r in records:
        if "event_type" not in r:
            r["event_type"] = _classify_event_type(prev, r)
        prev = r
    return records


def run(repo_root: Path, state_dir: Path, force_snapshot: bool = False) -> dict:
    base = base_state("agent5_proposal_intelligence", repo_root)
    repo_commit = base["repo_commit"]

    docs_state = read_json_or_none(state_dir / "docs_state.json")
    guardian_state = read_json_or_none(state_dir / "guardian_state.json")
    supervisor_state = read_json_or_none(state_dir / "supervisor_state.json")

    doc_status_by_path = {}
    if docs_state:
        doc_status_by_path = {d["path"]: d.get("status") for d in docs_state.get("documents", [])}

    guardian_findings = (guardian_state or {}).get("findings", [])
    guardian_has_critical = any(f.get("severity") == "critical" for f in guardian_findings)

    section_scores = []
    for criterion_id, spec in CRITERIA.items():
        text = _read_proposal_file(repo_root, spec["file"])
        doc_status = doc_status_by_path.get(spec["file"])
        result = _score_criterion(criterion_id, spec, text, doc_status, guardian_has_critical)
        result["evaluated_repo_commit"] = repo_commit
        section_scores.append(result)

    excellence = round((section_scores[0]["score"] + section_scores[1]["score"]) / 2, 2)
    impact = round((section_scores[2]["score"] + section_scores[3]["score"]) / 2, 2)
    implementation = round((section_scores[4]["score"] + section_scores[5]["score"]) / 2, 2)
    total = round(excellence + impact + implementation, 2)

    diagnostic_score = {
        "excellence": excellence, "impact": impact, "implementation": implementation,
        "total": total, "max_total": 15.0,
    }

    supervisor_degraded = (supervisor_state or {}).get("overall_status") == "DEGRADED"
    blocked = guardian_has_critical or supervisor_degraded

    prev_state = read_json_or_none(state_dir / STATE_FILENAME)
    prev_canonical_score = prev_state.get("canonical_score") if prev_state else None
    prev_promotion_status = prev_state.get("promotion_status") if prev_state else "PENDING_REVIEW"

    if blocked:
        promotion_status = "BLOCKED"
    elif prev_promotion_status == "APPROVED":
        promotion_status = "APPROVED"  # human-approved earlier and no new blocker -- never set by this agent
    else:
        promotion_status = "PENDING_REVIEW"

    canonical_score = prev_canonical_score  # NEVER auto-set/auto-promoted by this agent

    competitive_assessment = _competitive_assessment(section_scores, docs_state, guardian_state)
    weaknesses, evidence_packs, fix_packs = _build_improvement_loop(section_scores)

    fundability = _fundability_label(total, guardian_has_critical, promotion_status)

    overall_status = "BLOCKED" if blocked else "DIAGNOSTIC_COMPLETE"

    findings = []
    if guardian_has_critical:
        findings.append({"severity": "critical", "title": "Guardian critical finding blocks canonical promotion",
                          "description": "See Agent 3 (repository_guardian) state for details.", "source": "guardian_state.json"})
    if supervisor_degraded:
        findings.append({"severity": "high", "title": "Supervisor DEGRADED blocks canonical promotion",
                          "description": "See Agent 4 (project_supervisor) state for details.", "source": "supervisor_state.json"})

    prior_timeline = _read_timeline(state_dir)
    prev_record = prior_timeline[-1] if prior_timeline else None

    snapshot = {
        "timestamp": base["run_timestamp"],
        "repo_commit": repo_commit,
        "scoring_model_version": SCORING_MODEL_VERSION,
        "total": total, "excellence": excellence, "impact": impact, "implementation": implementation,
        "competitive_score": competitive_assessment["score"],
        "critical_finding_count": sum(1 for f in guardian_findings if f.get("severity") == "critical"),
        "promotion_status": promotion_status,
    }
    snapshot["event_type"] = _classify_event_type(prev_record, snapshot)
    _write_history_snapshot(state_dir, repo_commit, snapshot, force=force_snapshot)

    timeline = _read_timeline(state_dir)
    if timeline:
        baseline_rec, latest_rec = timeline[0], timeline[-1]
        timeline_summary = {
            "baseline": baseline_rec, "latest": latest_rec,
            "total_gain": round(latest_rec["total"] - baseline_rec["total"], 2),
            "snapshot_count": len(timeline),
        }
    else:
        timeline_summary = {"baseline": None, "latest": None, "total_gain": 0.0, "snapshot_count": 0}

    result = {
        **base,
        "status": "completed",
        "scoring_model_version": SCORING_MODEL_VERSION,
        "mode": "DIAGNOSTIC",
        "overall_status": overall_status,
        "fundability": fundability,
        "diagnostic_score": diagnostic_score,
        "canonical_score": canonical_score,
        "promotion_status": promotion_status,
        "section_scores": section_scores,
        "competitive_assessment": competitive_assessment,
        "weaknesses": weaknesses,
        "evidence_packs": evidence_packs,
        "fix_packs": fix_packs,
        "timeline_summary": timeline_summary,
        "findings": findings,
        "warnings": [],
        "errors": [],
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "proposal_intelligence")


if __name__ == "__main__":
    main()
