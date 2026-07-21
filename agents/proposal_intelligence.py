"""
agents/proposal_intelligence.py — Agent 5: Proposal Intelligence.

Fully independent of the old system's Agent 5: no import, no shared
state, no reading of old snapshots, no runtime dependency on it. This
module is deterministic and rule-based -- there is no hidden LLM call
anywhere in the scoring path.

STEP 2 OF 2 change: the PRIMARY diagnostic score is now evidence-gated
-- it is computed from pipeline/evidence_assembler.py's
proposal_evidence_state.json (coverage_ratio, evidence_quality,
contradictions, result), never from keyword/word-count signals in the
raw proposal text. The old keyword/word-count scorer still runs, but
only feeds a clearly-labelled SECONDARY metric,
text_completeness_score (STRUCTURAL_TEXT_COMPLETENESS /
NOT_EVALUATOR_SCORE) -- it is never used as diagnostic_score again.

This produces a DIAGNOSTIC score only -- never an official EC
evaluation, and never auto-promoted to a canonical/approved score.

Usage: python -m agents.proposal_intelligence
"""
import json
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state, read_json_or_none, now_iso

STATE_FILENAME = "proposal_intelligence_state.json"
EVIDENCE_STATE_FILENAME = "proposal_evidence_state.json"

# 2.0: evidence-gated scoring replaces keyword/word-count as the primary
# diagnostic_score (STEP 2 OF 2). Never reuse 1.x for this formula.
SCORING_MODEL_VERSION = "2.0"

# ── Section rubric (kept for the SECONDARY text-completeness metric only) ─
# Each criterion is scored out of 5.0 in 1.0-point increments (one point
# per matched signal), rounded to the nearest 0.5 by construction (all
# increments are 1.0, so no rounding is actually needed -- kept simple
# and auditable rather than clever). This is NOT the diagnostic score.
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
            (r"risk\s*register|\bR\d+\b\s*[-—]", "keyword_presence", "References a risk register"),
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
CRITERION_ORDER = list(CRITERIA.keys())  # E1, E2, I1, I2I3, IM1, IM2IM3

# ═══════════════════════════════════════════════════════════════════════
# PRIMARY: evidence-gated diagnostic score (STEP 2 OF 2)
# ═══════════════════════════════════════════════════════════════════════

# A. Evidence ceiling -- without a semantic evaluator, no criterion can
# auto-reach 5.0/5 from structural evidence presence alone.
EVIDENCE_CEILING = {
    "NONE": 1.0,
    "WEAK": 2.0,
    "PARTIAL": 3.0,
    "SUFFICIENT": 4.0,
    "STRONG": 4.5,
}

# D. Result penalties -- an additional ceiling on top of the evidence
# ceiling, driven by the evidence bundle's own per-criterion `result`.
RESULT_CEILING = {
    "CRITICAL": 2.0,
    "FAIL": 2.5,
    "WARN": 3.0,
    "REVIEW": 3.5,
    "OK": None,  # no additional ceiling
}

# C. Contradiction penalties, capped at 3.0 total per criterion.
CONTRADICTION_PENALTY = {
    "critical": 1.5,
    "high": 1.0,
    "medium": 0.5,
    "low": 0.25,
}
MAX_CONTRADICTION_PENALTY = 3.0

# Deterministic confidence-per-criterion baseline, discounted by
# freshness and contradiction count -- documented, never hidden.
QUALITY_BASE_CONFIDENCE = {
    "STRONG": 1.0, "SUFFICIENT": 0.85, "PARTIAL": 0.65, "WEAK": 0.45, "NONE": 0.2,
}


def _confidence_for_criterion(quality: str, freshness: str, contradiction_count: int) -> float:
    c = QUALITY_BASE_CONFIDENCE.get(quality, 0.2)
    if freshness != "FRESH":
        c -= 0.15
    c -= 0.05 * min(contradiction_count, 3)
    return round(max(0.1, min(1.0, c)), 2)


def _score_from_evidence(criterion_id: str, ce: dict, contradictions_by_id: dict) -> dict:
    """
    Locked formula (STEP 2 OF 2):
      base_score = coverage_ratio * 5
      criterion_score = min(base_score, evidence_ceiling)
      criterion_score -= contradiction_penalty (capped at 3.0)
      criterion_score = min(criterion_score, result_ceiling)   # if any
      criterion_score clamped to [0, 5], rounded to nearest 0.5

    Never uses word count. Never uses keyword presence as evidence --
    every input here (coverage_ratio, evidence_quality, contradictions,
    result) comes from pipeline/evidence_assembler.py, which itself
    only counts real canonical-file presence and cross-document
    consistency, never prose keyword matches.
    """
    coverage_ratio = ce.get("coverage_ratio", 0.0)
    quality = ce.get("evidence_quality", "NONE")
    result = ce.get("result", "FAIL")
    freshness = ce.get("freshness", "UNAVAILABLE")

    base_score = coverage_ratio * 5.0
    evidence_ceiling = EVIDENCE_CEILING.get(quality, EVIDENCE_CEILING["NONE"])
    score_after_evidence_ceiling = min(base_score, evidence_ceiling)

    contradiction_ids = ce.get("contradictions", [])
    contradiction_details = [contradictions_by_id[cid] for cid in contradiction_ids if cid in contradictions_by_id]
    raw_penalty = sum(CONTRADICTION_PENALTY.get(c.get("severity"), 0.0) for c in contradiction_details)
    contradiction_penalty = round(min(raw_penalty, MAX_CONTRADICTION_PENALTY), 2)
    score_after_contradictions = max(0.0, score_after_evidence_ceiling - contradiction_penalty)

    result_ceiling = RESULT_CEILING.get(result)
    score_before_round = min(score_after_contradictions, result_ceiling) if result_ceiling is not None else score_after_contradictions
    score_before_round = max(0.0, min(5.0, score_before_round))
    score = round(score_before_round * 2) / 2  # nearest 0.5

    confidence = _confidence_for_criterion(quality, freshness, len(contradiction_details))

    explanation = [f"coverage {coverage_ratio:.0%} -> base {base_score:.2f}/5",
                   f"evidence_quality={quality} caps at {evidence_ceiling}/5"]
    if contradiction_penalty:
        explanation.append(f"-{contradiction_penalty} for {len(contradiction_details)} contradiction(s)")
    if result_ceiling is not None:
        explanation.append(f"result={result} caps at {result_ceiling}/5")
    explanation.append(f"final {score}/5 (rounded to nearest 0.5)")

    return {
        "criterion_id": criterion_id,
        "title": CRITERIA[criterion_id]["title"],
        "score": score,
        "max_score": MAX_SCORE_PER_CRITERION,
        "coverage_ratio": coverage_ratio,
        "evidence_quality": quality,
        "evidence_ceiling": evidence_ceiling,
        "result_ceiling": result_ceiling,
        "contradiction_penalty": contradiction_penalty,
        "confidence": confidence,
        "supporting_sources": ce.get("supporting_sources", []),
        "missing_evidence": ce.get("missing_evidence", []),
        "contradictions": contradiction_details,
        "score_explanation": "; ".join(explanation),
        "freshness": freshness,
        "result": result,
    }


def _unavailable_criterion_score(criterion_id: str) -> dict:
    """proposal_evidence_state.json has no entry for this criterion (bundle
    missing or malformed) -- never silently default to a partial score."""
    return {
        "criterion_id": criterion_id,
        "title": CRITERIA[criterion_id]["title"],
        "score": 0.0,
        "max_score": MAX_SCORE_PER_CRITERION,
        "coverage_ratio": 0.0,
        "evidence_quality": "NONE",
        "evidence_ceiling": EVIDENCE_CEILING["NONE"],
        "result_ceiling": RESULT_CEILING["FAIL"],
        "contradiction_penalty": 0.0,
        "confidence": 0.1,
        "supporting_sources": [],
        "missing_evidence": [],
        "contradictions": [],
        "score_explanation": "proposal_evidence_state.json has no entry for this criterion -- "
                             "cannot score without the evidence bundle.",
        "freshness": "UNAVAILABLE",
        "result": "FAIL",
    }


# ═══════════════════════════════════════════════════════════════════════
# SECONDARY: structural text completeness (old keyword/word-count scorer,
# demoted -- STEP 2 OF 2). Kept only as a labelled, non-evaluator metric.
# ═══════════════════════════════════════════════════════════════════════

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
    """Keyword/word-count signal counter. This is NOT the diagnostic score
    -- its output only feeds text_completeness_score (see run())."""
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


# ═══════════════════════════════════════════════════════════════════════
# Competitive assessment -- rewritten (STEP 2 OF 2) to consume the real
# evidence bundle, never fixed differentiation keyword labels.
# ═══════════════════════════════════════════════════════════════════════

# "Differentiator" evidence -- concrete document existence, not word
# matching. These are the files that actually carry the project's
# technical/exploitation differentiation (PQC verification chain,
# threat model, IP position), not a grep for the words themselves.
DIFFERENTIATOR_TECHNICAL_KEYS = ["m1_technical_baseline", "security_target", "threat_model",
                                 "core_contract", "ip_classification"]

CALL_ALIGNMENT_CRITERIA = ("E1", "E2", "I1", "I2I3")

NOT_STARTED_LIKE = (None, "UNKNOWN", "NOT_STARTED")


def _partner_credibility(p: dict) -> float:
    """
    Weighted, deterministic. profile_status carries the only field this
    system currently has real (non-fabricated) evidence for; the other
    four sub-fields (PIC/commitment/personnel/budget) only earn credit
    once a structured partner-field parser exists to populate them --
    until then they are honestly UNKNOWN and contribute zero, per the
    STEP 1 rule "never fabricate partner facts."
    """
    s = 0.0
    if p.get("profile_status") == "PROFILE_PRESENT":
        s += 0.4
    if p.get("pic_status") not in NOT_STARTED_LIKE:
        s += 0.2
    if p.get("commitment_status") not in NOT_STARTED_LIKE:
        s += 0.2
    if p.get("personnel_status") not in NOT_STARTED_LIKE:
        s += 0.1
    if p.get("budget_status") not in NOT_STARTED_LIKE:
        s += 0.1
    return s


def _differentiation_score(evidence_bundle: dict, section_scores: list[dict]) -> float:
    technical = evidence_bundle.get("technical_readiness", {}) or {}
    present = sum(1 for k in DIFFERENTIATOR_TECHNICAL_KEYS if (technical.get(k) or {}).get("exists_non_empty"))
    total = len(DIFFERENTIATOR_TECHNICAL_KEYS)

    i2i3 = next((s for s in section_scores if s["criterion_id"] == "I2I3"), None)
    business_model_present = bool(i2i3) and any(
        src.get("key") == "business_model" for src in i2i3.get("supporting_sources", [])
    )
    total += 1
    if business_model_present:
        present += 1

    return round(present / total, 3) if total else 0.0


def _competitive_assessment(evidence_bundle: dict, section_scores: list[dict]) -> dict:
    """
    Deterministic 0-1 components, documented and testable -- no hidden
    LLM values, and (STEP 2 OF 2) no fixed keyword-presence labels for
    differentiation anymore. Every component is now derived from
    proposal_evidence_state.json, not re-derived from raw text or from
    docs_state.json's FROZEN/DRAFT status alone.
    """
    confidences = [s["confidence"] for s in section_scores]
    confidence_factor = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

    partners = evidence_bundle.get("partner_readiness", []) or []
    consortium_credibility = round(
        sum(_partner_credibility(p) for p in partners) / len(partners), 3
    ) if partners else 0.0

    # NOTE: evidence_assembler already folds every deduplicated Guardian
    # critical finding into `contradictions` (one contradiction-guardian-*
    # entry per canonical_issue_key) -- so `contradictions` alone is the
    # complete, deduplicated risk signal. guardian_summary.by_severity is
    # NOT added on top of this; doing so would double-count the same
    # underlying Guardian issues once via guardian_summary and once via
    # their corresponding contradiction entries.
    contradiction_sev_counts = {}
    for c in evidence_bundle.get("contradictions", []) or []:
        sev = c.get("severity", "low")
        contradiction_sev_counts[sev] = contradiction_sev_counts.get(sev, 0) + 1
    critical_count = contradiction_sev_counts.get("critical", 0)
    high_count = contradiction_sev_counts.get("high", 0)
    medium_count = contradiction_sev_counts.get("medium", 0)
    low_count = contradiction_sev_counts.get("low", 0)
    risk_perception = round(max(0.0, 1.0 - min(
        1.0, critical_count * 0.4 + high_count * 0.15 + medium_count * 0.05 + low_count * 0.02
    )), 3)

    call_alignment = [s for s in section_scores if s["criterion_id"] in CALL_ALIGNMENT_CRITERIA]
    avg_coverage = (sum(s.get("coverage_ratio", 0.0) for s in call_alignment) / len(call_alignment)) if call_alignment else 0.0
    technical = evidence_bundle.get("technical_readiness", {}) or {}
    tech_entries = [v for v in technical.values() if isinstance(v, dict) and "exists_non_empty" in v]
    tech_fraction = (sum(1 for v in tech_entries if v["exists_non_empty"]) / len(tech_entries)) if tech_entries else 0.0
    strategic_fit = round(0.6 * avg_coverage + 0.4 * tech_fraction, 3)

    differentiation = _differentiation_score(evidence_bundle, section_scores)

    components = {
        "confidence_factor": {"score": confidence_factor,
                              "rationale": "Average evidence-gated confidence across the six diagnostic criteria.",
                              "evidence": [], "blockers": []},
        "consortium_credibility": {"score": consortium_credibility,
                                   "rationale": "Weighted partner_readiness: profile presence + PIC/commitment/personnel/budget status (never fabricated).",
                                   "evidence": [], "blockers": [] if partners else ["No partner_readiness data (evidence bundle unavailable)."]},
        "risk_perception": {"score": risk_perception,
                            "rationale": "Evidence-bundle contradictions by severity (Guardian's deduplicated critical findings are already folded into these contradictions -- not counted twice).",
                            "evidence": [], "blockers": [f"{critical_count} critical, {high_count} high risk signal(s)."] if (critical_count or high_count) else []},
        "strategic_fit": {"score": strategic_fit,
                          "rationale": "60% E1/E2/I1/I2I3 evidence coverage + 40% technical_readiness fraction.",
                          "evidence": [], "blockers": []},
        "differentiation": {"score": differentiation,
                            "rationale": "Fraction of concrete differentiator evidence files present (technical baseline, security target, threat model, core contract, IP classification, business model) -- not keyword matching.",
                            "evidence": [], "blockers": []},
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
            "title": f"{s['title']} under-evidenced", "description": s["score_explanation"],
            "evidence": s["supporting_sources"], "confidence": s["confidence"], "status": "OPEN",
        })
        gaps = [m.get("label", m.get("key", "?")) for m in s["missing_evidence"]]
        evidence_packs.append({
            "weakness_id": weakness_id,
            "source_documents": [CRITERIA[s["criterion_id"]]["file"]],
            "best_evidence": s["supporting_sources"],
            "evidence_score": s["confidence"],
            "gaps": gaps,
        })
        fix_packs.append({
            "id": f"fixpack-{s['criterion_id']}",
            "weakness_id": weakness_id,
            "criterion": s["criterion_id"],
            "proposed_action": f"Add/repair evidence for: {', '.join(gaps)}." if gaps else "Resolve outstanding contradictions.",
            "affected_files": [CRITERIA[s["criterion_id"]]["file"]],
            "required_evidence": gaps,
            "status": "PENDING_REVIEW",
            "human_approval_required": True,
        })
    return weaknesses, evidence_packs, fix_packs


def _fundability_label(total: float, blocked: bool) -> str:
    """
    Never returns "FUNDABLE" -- that label does not exist in this system.
    BLOCKED takes precedence over the numeric total regardless of score.
    """
    if blocked:
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
    REPOSITORY_CHANGE    -- repo_commit differs from the previous snapshot
                             (or this is the first-ever snapshot).
    MODEL_RECALCULATION  -- same repo_commit, but scoring_model_version
                             changed (e.g. a rubric bugfix or, as in STEP 2,
                             the keyword scorer being replaced entirely --
                             never counted as proposal improvement).
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


def _build_timeline_summary(state_dir: Path) -> dict:
    """
    total_gain is computed ONLY across REPOSITORY_CHANGE events -- a
    MODEL_RECALCULATION (same commit, new scoring_model_version, e.g.
    this very STEP 2 deploy) must never be read as proposal improvement.
    """
    timeline = _read_timeline(state_dir)
    repo_change_records = [r for r in timeline if r.get("event_type") == "REPOSITORY_CHANGE"]
    model_recalculation_count = sum(1 for r in timeline if r.get("event_type") == "MODEL_RECALCULATION")

    if repo_change_records:
        baseline_rec = repo_change_records[0]
        latest_repo_change_rec = repo_change_records[-1]
        total_gain = round(latest_repo_change_rec["total"] - baseline_rec["total"], 2)
    else:
        baseline_rec = None
        total_gain = 0.0

    return {
        "baseline": baseline_rec,
        "latest": timeline[-1] if timeline else None,
        "total_gain": total_gain,
        "snapshot_count": len(timeline),
        "repository_change_count": len(repo_change_records),
        "model_recalculation_count": model_recalculation_count,
    }


def run(repo_root: Path, state_dir: Path, force_snapshot: bool = False) -> dict:
    base = base_state("agent5_proposal_intelligence", repo_root)
    repo_commit = base["repo_commit"]

    docs_state = read_json_or_none(state_dir / "docs_state.json")
    guardian_state = read_json_or_none(state_dir / "guardian_state.json")
    supervisor_state = read_json_or_none(state_dir / "supervisor_state.json")
    evidence_bundle = read_json_or_none(state_dir / EVIDENCE_STATE_FILENAME)

    doc_status_by_path = {}
    if docs_state:
        doc_status_by_path = {d["path"]: d.get("status") for d in docs_state.get("documents", [])}

    guardian_findings = (guardian_state or {}).get("findings", [])
    guardian_has_critical = any(f.get("severity") == "critical" for f in guardian_findings)

    # ── SECONDARY: structural text completeness (demoted) ──────────────
    text_sections = []
    for criterion_id, spec in CRITERIA.items():
        text = _read_proposal_file(repo_root, spec["file"])
        doc_status = doc_status_by_path.get(spec["file"])
        r = _score_criterion(criterion_id, spec, text, doc_status, guardian_has_critical)
        r["evaluated_repo_commit"] = repo_commit
        text_sections.append(r)
    tc_excellence = round((text_sections[0]["score"] + text_sections[1]["score"]) / 2, 2)
    tc_impact = round((text_sections[2]["score"] + text_sections[3]["score"]) / 2, 2)
    tc_implementation = round((text_sections[4]["score"] + text_sections[5]["score"]) / 2, 2)
    text_completeness_score = {
        "label": "STRUCTURAL_TEXT_COMPLETENESS",
        "warning": "NOT_EVALUATOR_SCORE",
        "excellence": tc_excellence, "impact": tc_impact, "implementation": tc_implementation,
        "total": round(tc_excellence + tc_impact + tc_implementation, 2),
        "max_total": 15.0,
        "sections": text_sections,
    }

    # ── PRIMARY: evidence-gated diagnostic score ────────────────────────
    evidence_bundle_available = evidence_bundle is not None
    evidence_bundle_repo_commit = evidence_bundle.get("repo_commit") if evidence_bundle else None
    evidence_bundle_stale = bool(evidence_bundle_available and evidence_bundle_repo_commit != repo_commit)
    evidence_bundle_freshness = "UNAVAILABLE" if not evidence_bundle_available else ("STALE" if evidence_bundle_stale else "FRESH")

    contradictions_by_id = {c["id"]: c for c in (evidence_bundle or {}).get("contradictions", [])}
    ce_by_id = {ce["criterion_id"]: ce for ce in (evidence_bundle or {}).get("criterion_evidence", [])}

    section_scores = []
    for criterion_id in CRITERION_ORDER:
        ce = ce_by_id.get(criterion_id)
        section_scores.append(
            _score_from_evidence(criterion_id, ce, contradictions_by_id) if ce is not None
            else _unavailable_criterion_score(criterion_id)
        )

    excellence = round((section_scores[0]["score"] + section_scores[1]["score"]) / 2, 2)
    impact = round((section_scores[2]["score"] + section_scores[3]["score"]) / 2, 2)
    implementation = round((section_scores[4]["score"] + section_scores[5]["score"]) / 2, 2)
    total = round(excellence + impact + implementation, 2)

    diagnostic_score = {
        "excellence": excellence, "impact": impact, "implementation": implementation,
        "total": total, "max_total": 15.0,
    }

    evidence_result_critical = bool(evidence_bundle and evidence_bundle.get("result") == "CRITICAL")
    supervisor_status = (supervisor_state or {}).get("overall_status")
    supervisor_blocked = supervisor_status in ("DEGRADED", "CRITICAL")

    prev_state = read_json_or_none(state_dir / STATE_FILENAME)
    prev_canonical_score = prev_state.get("canonical_score") if prev_state else None
    prev_promotion_status = prev_state.get("promotion_status") if prev_state else "PENDING_REVIEW"

    blocked = bool(guardian_has_critical or supervisor_blocked or evidence_result_critical or not evidence_bundle_available)

    if blocked:
        promotion_status = "BLOCKED"
    elif prev_promotion_status == "APPROVED":
        promotion_status = "APPROVED"  # human-approved earlier and no new blocker -- never set by this agent
    else:
        promotion_status = "PENDING_REVIEW"

    canonical_score = prev_canonical_score  # NEVER auto-set/auto-promoted by this agent

    competitive_assessment = _competitive_assessment(evidence_bundle or {}, section_scores)
    weaknesses, evidence_packs, fix_packs = _build_improvement_loop(section_scores)

    fundability = _fundability_label(total, blocked)

    if not evidence_bundle_available:
        overall_status = "EVIDENCE_UNAVAILABLE"
    elif blocked:
        overall_status = "BLOCKED"
    else:
        overall_status = "DIAGNOSTIC_COMPLETE"

    findings = []
    if guardian_has_critical:
        findings.append({"severity": "critical", "title": "Guardian critical finding blocks canonical promotion",
                          "description": "See Agent 3 (repository_guardian) state for details.", "source": "guardian_state.json"})
    if supervisor_blocked:
        findings.append({"severity": "high", "title": f"Supervisor {supervisor_status} blocks canonical promotion",
                          "description": "See Agent 4 (project_supervisor) state for details.", "source": "supervisor_state.json"})
    if evidence_result_critical:
        findings.append({"severity": "critical", "title": "Evidence bundle result is CRITICAL",
                          "description": "See proposal_evidence_state.json for details.", "source": EVIDENCE_STATE_FILENAME})
    if not evidence_bundle_available:
        findings.append({"severity": "critical", "title": "Evidence bundle unavailable",
                          "description": "pipeline.evidence_assembler has not run yet -- diagnostic score cannot be evidence-gated.",
                          "source": EVIDENCE_STATE_FILENAME})
    elif evidence_bundle_stale:
        findings.append({"severity": "medium", "title": "Evidence bundle STALE",
                          "description": f"evidence bundle repo_commit={evidence_bundle_repo_commit} != live repo_commit={repo_commit}.",
                          "source": EVIDENCE_STATE_FILENAME})

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

    timeline_summary = _build_timeline_summary(state_dir)

    result = {
        **base,
        "status": "completed",
        "scoring_model_version": SCORING_MODEL_VERSION,
        "evidence_model_version": (evidence_bundle or {}).get("evidence_model_version"),
        "evidence_bundle_repo_commit": evidence_bundle_repo_commit,
        "evidence_bundle_available": evidence_bundle_available,
        "evidence_bundle_freshness": evidence_bundle_freshness,
        "mode": "DIAGNOSTIC",
        "overall_status": overall_status,
        "fundability": fundability,
        "diagnostic_score": diagnostic_score,
        "text_completeness_score": text_completeness_score,
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
