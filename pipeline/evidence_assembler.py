"""
pipeline/evidence_assembler.py

Read-only pipeline component -- deliberately NOT a sixth "agent". It
starts nothing, calls no LLM, computes no proposal score, and never
writes to the proposal repository. It reads:

  - the proposal repo's canonical documents (existence + non-empty
    content only -- this module never scores prose quality, and never
    treats a keyword match alone as evidence, per the STEP 1 brief);
  - the state JSON already written by the FORISEC agents under
    FORISEC_STATE_DIR (docs_state, evaluation_state, guardian_state,
    supervisor_state, budget_state).

...and produces one normalized bundle, proposal_evidence_state.json,
describing what evidence exists for each Horizon-Europe RIA criterion,
where it lives, whether it is fresh, and where it contradicts itself.

Explicitly OUT OF SCOPE for this step (STEP 2 work):
  - computing or replacing the Agent 5 diagnostic score;
  - invoking any LLM;
  - writing to the proposal repository;
  - starting/orchestrating any of the five agents (it only reads their
    already-written state files).

Usage: python -m pipeline.evidence_assembler
"""
import re
from pathlib import Path

from agents.common import atomic_write_json, get_repo_commit, now_iso, read_json_or_none

SCHEMA_VERSION = "1.0"
EVIDENCE_MODEL_VERSION = "1.0"
STATE_FILENAME = "proposal_evidence_state.json"

FRESHNESS_VALUES = ("FRESH", "STALE", "UNAVAILABLE")
RESULT_VALUES = ("OK", "REVIEW", "WARN", "FAIL", "CRITICAL")
RESULT_PRECEDENCE = {"CRITICAL": 4, "FAIL": 3, "WARN": 2, "REVIEW": 1, "OK": 0}
FRESHNESS_PRECEDENCE = {"UNAVAILABLE": 2, "STALE": 1, "FRESH": 0}

# ---------------------------------------------------------------------------
# Which agent state file backs which named source. Read-only -- this module
# never re-derives what a producer agent already computed; it only reads it.
# ---------------------------------------------------------------------------
SOURCE_STATE_FILES = {
    "docs": "docs_state.json",
    "evaluation": "evaluation_state.json",
    "guardian": "guardian_state.json",
    "supervisor": "supervisor_state.json",
    "budget": "budget_state.json",
}


def _worst_result(*results) -> str:
    best = "OK"
    for r in results:
        if r and RESULT_PRECEDENCE.get(r, 0) > RESULT_PRECEDENCE.get(best, 0):
            best = r
    return best


def _worst_freshness(*freshnesses) -> str:
    worst = "FRESH"
    for f in freshnesses:
        if f and FRESHNESS_PRECEDENCE.get(f, 0) > FRESHNESS_PRECEDENCE.get(worst, 0):
            worst = f
    return worst


def _read_repo_file(repo_root: Path, rel_path: str) -> str | None:
    """None means missing OR whitespace-only (a 0-byte scaffold file is
    not evidence -- same convention docs_controller.py already uses)."""
    full = repo_root / rel_path
    if not full.exists() or not full.is_file():
        return None
    try:
        text = full.read_text(encoding="utf-8")
    except Exception:
        return None
    return text if text.strip() else None


def _file_available(repo_root: Path, rel_path: str) -> bool:
    return _read_repo_file(repo_root, rel_path) is not None


# ---------------------------------------------------------------------------
# Source-state read + normalize
# ---------------------------------------------------------------------------

def _docs_result(raw: dict) -> str:
    return {"ON_TRACK": "OK", "ON_TRACK_WITH_DRAFTS": "REVIEW", "WARN": "WARN", "FAIL": "FAIL"}.get(
        raw.get("overall_status"), "REVIEW")


def _evaluation_result(raw: dict) -> str:
    return "OK" if raw.get("status") == "completed" else "REVIEW"


def _guardian_result(raw: dict) -> str:
    findings = raw.get("findings", [])
    if any(f.get("severity") == "critical" for f in findings):
        return "CRITICAL"
    return {"PASS": "OK", "WARN": "WARN", "FAIL": "FAIL"}.get(raw.get("guardian_status"), "REVIEW")


def _supervisor_result(raw: dict) -> str:
    return {"OK": "OK", "REVIEW": "REVIEW", "DEGRADED": "FAIL", "CRITICAL": "CRITICAL"}.get(
        raw.get("overall_status"), "REVIEW")


def _budget_result(raw: dict) -> str:
    if not raw.get("available"):
        return "FAIL"
    return "WARN" if raw.get("any_missing") else "OK"


_RESULT_FN = {
    "docs": _docs_result, "evaluation": _evaluation_result, "guardian": _guardian_result,
    "supervisor": _supervisor_result, "budget": _budget_result,
}


def _read_source_states(state_dir: Path, live_commit: str | None) -> dict:
    out = {}
    for name, filename in SOURCE_STATE_FILES.items():
        raw = read_json_or_none(state_dir / filename)
        if raw is None:
            out[name] = {"available": False, "freshness": "UNAVAILABLE", "result": "FAIL",
                         "repo_commit": None, "source_file": filename}
            continue
        recorded_commit = raw.get("repo_commit")
        if live_commit and recorded_commit:
            freshness = "FRESH" if live_commit == recorded_commit else "STALE"
        else:
            freshness = "UNAVAILABLE"
        result = _RESULT_FN[name](raw)
        out[name] = {"available": True, "freshness": freshness, "result": result,
                     "repo_commit": recorded_commit, "source_file": filename}
    return out


# ---------------------------------------------------------------------------
# Criterion evidence requirements. Every item is a real, existing (or
# genuinely absent -- never invented) canonical path or state key.
# type: "file" (single canonical doc), "state" (an agent state fact).
# ---------------------------------------------------------------------------
def _wp_files():
    return [
        ("wp1_description", "01_work_packages/WP1_PROJECT_MANAGEMENT.md"),
        ("wp2_description", "01_work_packages/WP2_PQC_PLATFORM.md"),
        ("wp3_description", "01_work_packages/WP3_EMBEDDED_PROVENANCE.md"),
        ("wp4_description", "01_work_packages/WP4_FPGA_HARDWARE_SECURITY.md"),
        ("wp5_description", "01_work_packages/WP5_OPERATIONAL_PILOT.md"),
        ("wp6_description", "01_work_packages/WP6_EXPLOITATION_STANDARDISATION.md"),
    ]


def _partner_files():
    return [
        ("partner_foritech", "05_partners/FORITECH.md"),
        ("partner_rtu", "05_partners/RTU.md"),
        ("partner_logiicdev", "05_partners/LOGIICDEV.md"),
        ("partner_solarix", "05_partners/SOLARIX.md"),
    ]


def _criterion_requirements():
    baseline_evidence = [
        ("m1_technical_baseline", "file", "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md", "M1 technical baseline (TRL evidence)"),
        ("security_target", "file", "docs/technical/FORITECH_SECURITY_TARGET.md", "Security target"),
        ("threat_model", "file", "docs/technical/FORITECH_THREAT_MODEL.md", "Threat model"),
        ("task_truth_matrix", "file", "00_baseline/MASTER_TASK_TRUTH_MATRIX.md", "Master task truth matrix"),
    ]
    return {
        "E1": [
            ("excellence_text", "file", "04_proposal/EXCELLENCE.md", "Excellence narrative"),
            *baseline_evidence,
            ("guardian_clean", "state", "guardian", "No unresolved Guardian critical findings"),
        ],
        "E2": [
            ("excellence_text", "file", "04_proposal/EXCELLENCE.md", "Excellence narrative"),
            *baseline_evidence,
            ("guardian_clean", "state", "guardian", "No unresolved Guardian critical findings"),
        ],
        "I1": [
            ("impact_text", "file", "04_proposal/IMPACT.md", "Impact narrative"),
            ("milestone_register", "file", "02_registers/MILESTONE_REGISTER.md", "Milestone register (KPI/outcome timing)"),
            ("business_model", "file", "docs/business/FORITECH_BUSINESS_MODEL.md", "Business model (target groups/stakeholders)"),
        ],
        "I2I3": [
            ("impact_text", "file", "04_proposal/IMPACT.md", "Impact narrative"),
            ("business_model", "file", "docs/business/FORITECH_BUSINESS_MODEL.md", "Business model (exploitation evidence)"),
            ("ip_classification", "file", "00_baseline/FORITECH_IP_OUTPUT_CLASSIFICATION.md", "IP output classification"),
        ],
        "IM1": [
            ("implementation_text", "file", "04_proposal/IMPLEMENTATION.md", "Implementation narrative"),
            *[(k, "file", p, f"WP detailed description ({k})") for k, p in _wp_files()],
            ("task_register", "file", "00_baseline/WP_TASK_REGISTER_DRAFT.md", "WP task register (draft)"),
            ("deliverable_register", "file", "02_registers/DELIVERABLE_REGISTER.md", "Deliverable register"),
            ("milestone_register", "file", "02_registers/MILESTONE_REGISTER.md", "Milestone register"),
            ("dependency_map", "file", "03_implementation/DEPENDENCY_MAP.md", "Dependency map"),
            ("sequencing_matrix", "file", "02_registers/WORK_PLAN_SEQUENCING_MATRIX.md", "Work plan sequencing matrix / Gantt cross-reference"),
            ("gantt", "file", "03_implementation/GANTT.md", "Gantt"),
            ("risk_register", "none_declared", None, "Standalone risk register (no canonical file is declared in config/canonical_documents.json -- risk content, if any, is only embedded prose inside other registers, not a structured/verifiable register)"),
        ],
        "IM2IM3": [
            ("implementation_text", "file", "04_proposal/IMPLEMENTATION.md", "Implementation narrative"),
            ("consortium_structure", "file", "00_baseline/CONSORTIUM_AND_WP_STRUCTURE.md", "Consortium and WP structure"),
            *[(k, "file", p, f"Partner profile ({k})") for k, p in _partner_files()],
            ("pm_allocation", "file", "03_implementation/PM_ALLOCATION.md", "PM allocation"),
            ("budget_state", "state", "budget", "Reconciled budget state (budget_reader, all 6 WP repos parsed)"),
            ("guardian_clean", "state", "guardian", "No unresolved Guardian critical findings"),
        ],
    }


def _is_available(item, repo_root: Path, source_states: dict) -> bool:
    key, kind, path, _label = item
    if kind == "file":
        return _file_available(repo_root, path)
    if kind == "state":
        st = source_states.get(path, {})
        if not st.get("available"):
            return False
        if path == "guardian":
            # "clean" is a content fact, not just "the file exists" --
            # re-derive from result, never invent a pass.
            return st.get("result") not in ("CRITICAL", "FAIL")
        if path == "budget":
            # A budget claim only counts as reconciled evidence if all 6
            # WP repos were actually parsed -- "available" alone is not
            # enough (mirrors STEP 1 finding: the word "budget" or a
            # partially-parsed state must never count as full evidence).
            return st.get("result") == "OK"
        return st.get("result") in ("OK", "REVIEW")
    return False  # none_declared


def _quality(coverage_ratio: float, contradiction_count: int, freshness: str) -> str:
    if coverage_ratio <= 0:
        return "NONE"
    if contradiction_count > 0:
        return "WEAK" if coverage_ratio < 0.75 else "PARTIAL"
    if coverage_ratio < 0.5:
        return "WEAK"
    if coverage_ratio < 0.75:
        return "PARTIAL"
    if coverage_ratio < 1.0:
        return "SUFFICIENT"
    return "STRONG" if freshness == "FRESH" else "SUFFICIENT"


def _result_for_quality(quality: str) -> str:
    return {"NONE": "FAIL", "WEAK": "WARN", "PARTIAL": "REVIEW", "SUFFICIENT": "OK", "STRONG": "OK"}[quality]


def _build_criterion_evidence(repo_root: Path, source_states: dict, contradictions_by_criterion: dict,
                               global_freshness: str) -> list:
    out = []
    for cid, items in _criterion_requirements().items():
        required = [{"key": k, "type": t, "path": p, "label": lbl} for k, t, p, lbl in items]
        available, missing, supporting = [], [], []
        for item in items:
            key, kind, path, label = item
            if _is_available(item, repo_root, source_states):
                available.append({"key": key, "label": label})
                if kind == "file":
                    supporting.append({"key": key, "path": path})
                elif kind == "state":
                    supporting.append({"key": key, "state_source": path})
            else:
                reason = ("no canonical source declared" if kind == "none_declared"
                          else f"{path} missing or empty" if kind == "file"
                          else f"{path} state unavailable or not clean")
                missing.append({"key": key, "label": label, "reason": reason})

        coverage_ratio = round(len(available) / len(required), 3) if required else 0.0
        crit_contradictions = contradictions_by_criterion.get(cid, [])

        # freshness for this criterion: worst of (global source freshness,
        # UNAVAILABLE if nothing at all is available)
        if not available:
            crit_freshness = "UNAVAILABLE"
        else:
            crit_freshness = global_freshness

        quality = _quality(coverage_ratio, len(crit_contradictions), crit_freshness)
        base_result = _result_for_quality(quality)
        if any(c["severity"] == "critical" for c in crit_contradictions):
            result = "CRITICAL"
        else:
            result = base_result

        out.append({
            "criterion_id": cid,
            "required_evidence": required,
            "available_evidence": available,
            "missing_evidence": missing,
            "contradictions": [c["id"] for c in crit_contradictions],
            "supporting_sources": supporting,
            "coverage_ratio": coverage_ratio,
            "evidence_quality": quality,
            "freshness": crit_freshness,
            "result": result,
        })
    return out


# ---------------------------------------------------------------------------
# Cross-document contradiction checks (deterministic, all 8 required types)
# ---------------------------------------------------------------------------

def _mentions(text: str | None, pattern: str) -> bool:
    return bool(text) and re.search(pattern, text, re.IGNORECASE) is not None


def _check_budget_reconciled(repo_root, source_states, repo_commit):
    impl = _read_repo_file(repo_root, "04_proposal/IMPLEMENTATION.md")
    budget = source_states.get("budget", {})
    reconciled = budget.get("available") and budget.get("result") == "OK"
    claims = _mentions(impl, r"\bbudget\b")
    passed = not claims or reconciled
    contradiction = None
    if claims and not reconciled:
        contradiction = {
            "id": "contradiction-budget-not-reconciled", "criterion": "IM2IM3", "severity": "high",
            "claim_source": "04_proposal/IMPLEMENTATION.md",
            "claim": "Narrative references budget/lump-sum figures.",
            "contradicting_source": "budget_state.json",
            "reason": "budget_state.json is unavailable, or budget_reader could not parse all 6 WP repos -- "
                      "the word 'budget' appearing in the proposal text is not itself budget evidence.",
            "affected_files": ["04_proposal/IMPLEMENTATION.md", "budget_state.json"],
            "repo_commit": repo_commit,
        }
    return passed, contradiction


def _check_consortium_partner_profiles(repo_root, repo_commit):
    impl = _read_repo_file(repo_root, "04_proposal/IMPLEMENTATION.md")
    empty_partners = [p for k, p in _partner_files() if not _file_available(repo_root, p)]
    claims = _mentions(impl, r"\bconsortium\b|\bpartner\b")
    passed = not claims or not empty_partners
    contradiction = None
    if claims and empty_partners:
        contradiction = {
            "id": "contradiction-consortium-empty-partner-profiles", "criterion": "IM2IM3", "severity": "high",
            "claim_source": "04_proposal/IMPLEMENTATION.md",
            "claim": "Narrative references the consortium/partners as an established fact.",
            "contradicting_source": ", ".join(empty_partners),
            "reason": f"{len(empty_partners)} of 4 partner profile file(s) under 05_partners/ are empty "
                      f"(0 bytes) -- an empty file is missing evidence, not a completed profile.",
            "affected_files": empty_partners,
            "repo_commit": repo_commit,
        }
    return passed, contradiction


def _check_pm_allocation(repo_root, repo_commit):
    impl = _read_repo_file(repo_root, "04_proposal/IMPLEMENTATION.md")
    pm_alloc_available = _file_available(repo_root, "03_implementation/PM_ALLOCATION.md")
    claims = _mentions(impl, r"person[- ]month|\bPM\b")
    passed = not claims or pm_alloc_available
    contradiction = None
    if claims and not pm_alloc_available:
        contradiction = {
            "id": "contradiction-pm-claim-no-allocation", "criterion": "IM2IM3", "severity": "medium",
            "claim_source": "04_proposal/IMPLEMENTATION.md",
            "claim": "Narrative references person-months / PM effort.",
            "contradicting_source": "03_implementation/PM_ALLOCATION.md",
            "reason": "PM_ALLOCATION.md is missing or empty -- person-month effort is not independently verifiable.",
            "affected_files": ["04_proposal/IMPLEMENTATION.md", "03_implementation/PM_ALLOCATION.md"],
            "repo_commit": repo_commit,
        }
    return passed, contradiction


def _check_deliverable_milestone_citations(repo_root, repo_commit):
    impl = _read_repo_file(repo_root, "04_proposal/IMPLEMENTATION.md") or ""
    deliverable_register = _read_repo_file(repo_root, "02_registers/DELIVERABLE_REGISTER.md") or ""
    milestone_register = _read_repo_file(repo_root, "02_registers/MILESTONE_REGISTER.md") or ""

    cited_d = set(re.findall(r"\bD\d+\.\d+\b", impl))
    registered_d = set(re.findall(r"\bD\d+\.\d+\b", deliverable_register))
    missing_d = sorted(cited_d - registered_d)

    cited_m = set(re.findall(r"\bM\d+\b", impl))
    registered_m = set(re.findall(r"\bM\d+\b", milestone_register))
    missing_m = sorted(cited_m - registered_m)

    passed = not missing_d and not missing_m
    contradiction = None
    if not passed:
        contradiction = {
            "id": "contradiction-deliverable-milestone-not-registered", "criterion": "IM1", "severity": "medium",
            "claim_source": "04_proposal/IMPLEMENTATION.md",
            "claim": f"Cites deliverable(s) {missing_d or '(none)'} / milestone(s) {missing_m or '(none)'}.",
            "contradicting_source": "02_registers/DELIVERABLE_REGISTER.md, 02_registers/MILESTONE_REGISTER.md",
            "reason": "One or more cited deliverable/milestone IDs do not appear in the canonical register.",
            "affected_files": ["04_proposal/IMPLEMENTATION.md", "02_registers/DELIVERABLE_REGISTER.md",
                               "02_registers/MILESTONE_REGISTER.md"],
            "repo_commit": repo_commit,
        }
    return passed, contradiction


_NEGATION_WP_RE = re.compile(r"\bno\s+wp\s*(\d+)\b", re.IGNORECASE)
_WP_CITE_RE = re.compile(r"\bWP\s*(\d+)\b", re.IGNORECASE)


def _check_wp_numbering(repo_root, repo_commit):
    impl = _read_repo_file(repo_root, "04_proposal/IMPLEMENTATION.md") or ""
    existing_wps = {int(re.search(r"WP(\d+)", p).group(1)) for _k, p in _wp_files()}
    negated = {int(n) for n in _NEGATION_WP_RE.findall(impl)}
    cited = {int(n) for n in _WP_CITE_RE.findall(impl)}
    # A WP number explicitly disclaimed ("No WP7 is part of...") is not a
    # citation of a real work package -- exclude it, don't false-positive.
    unexplained = sorted(cited - existing_wps - negated)
    passed = not unexplained
    contradiction = None
    if not passed:
        contradiction = {
            "id": "contradiction-wp-number-mismatch", "criterion": "IM1", "severity": "medium",
            "claim_source": "04_proposal/IMPLEMENTATION.md",
            "claim": f"References WP number(s) {unexplained} not among the 6 canonical WP description files.",
            "contradicting_source": "01_work_packages/*.md",
            "reason": "A cited WP number has no corresponding canonical WP description file (WP1-WP6 only).",
            "affected_files": ["04_proposal/IMPLEMENTATION.md"],
            "repo_commit": repo_commit,
        }
    return passed, contradiction


_TRL_CLAIM_RE = re.compile(r"TRL\s*(\d)", re.IGNORECASE)


def _check_trl_claim(repo_root, repo_commit):
    excellence = _read_repo_file(repo_root, "04_proposal/EXCELLENCE.md") or ""
    baseline = _read_repo_file(repo_root, "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md")
    m = _TRL_CLAIM_RE.search(excellence)
    passed = True
    contradiction = None
    if m:
        claimed = m.group(1)
        supported = bool(baseline) and re.search(r"TRL\s*" + re.escape(claimed), baseline, re.IGNORECASE)
        passed = bool(supported)
        if not passed:
            contradiction = {
                "id": "contradiction-trl-claim-unsupported", "criterion": "E1", "severity": "high",
                "claim_source": "04_proposal/EXCELLENCE.md",
                "claim": f"Claims starting TRL {claimed}.",
                "contradicting_source": "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md",
                "reason": "The technical baseline is missing, empty, or does not corroborate the same TRL figure.",
                "affected_files": ["04_proposal/EXCELLENCE.md", "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md"],
                "repo_commit": repo_commit,
            }
    return passed, contradiction


def _criterion_for_guardian_path(path: str) -> str:
    if path.startswith("05_partners/"):
        return "IM2IM3"
    if path.startswith(("02_registers/", "03_implementation/", "01_work_packages/", "00_baseline/task_descriptions/")):
        return "IM1"
    if path.startswith(("docs/technical/", "00_baseline/")):
        return "E1"
    return "GLOBAL"


def _check_guardian_broken_references(source_states, guardian_raw, repo_commit):
    findings = (guardian_raw or {}).get("findings", [])
    critical = [f for f in findings if f.get("severity") == "critical"]
    passed = not critical
    contradictions = []
    for f in critical:
        source = f.get("source", "")
        contradictions.append({
            "id": f"contradiction-guardian-{f.get('canonical_issue_key', f.get('id', 'unknown'))}",
            "criterion": _criterion_for_guardian_path(source),
            "severity": "critical",
            "claim_source": source,
            "claim": "Contains a cross-reference to another canonical document.",
            "contradicting_source": f.get("target", f.get("title", "")),
            "reason": f.get("description", "Broken self-reference (Guardian)."),
            "affected_files": [s.get("source_file") for s in f.get("affected_sources", [])] or [source],
            "repo_commit": repo_commit,
        })
    return passed, contradictions


def _check_state_commit_mismatch(source_states, repo_commit):
    stale = [name for name, st in source_states.items() if st.get("freshness") == "STALE"]
    passed = not stale
    contradictions = []
    for name in stale:
        st = source_states[name]
        contradictions.append({
            "id": f"contradiction-stale-state-{name}", "criterion": "GLOBAL", "severity": "medium",
            "claim_source": st.get("source_file", name),
            "claim": f"{name} state was last computed at a different commit than the live proposal HEAD.",
            "contradicting_source": "live repo HEAD",
            "reason": f"Recorded repo_commit={st.get('repo_commit')} != live HEAD={repo_commit}.",
            "affected_files": [st.get("source_file", name)],
            "repo_commit": repo_commit,
        })
    return passed, contradictions


def _run_cross_document_checks(repo_root, source_states, guardian_raw, repo_commit):
    checks = []
    contradictions = []

    passed, c = _check_budget_reconciled(repo_root, source_states, repo_commit)
    checks.append({"check_id": "budget_claim_reconciled", "passed": passed})
    if c:
        contradictions.append(c)

    passed, c = _check_consortium_partner_profiles(repo_root, repo_commit)
    checks.append({"check_id": "consortium_claim_partner_profiles", "passed": passed})
    if c:
        contradictions.append(c)

    passed, c = _check_pm_allocation(repo_root, repo_commit)
    checks.append({"check_id": "pm_claim_pm_allocation", "passed": passed})
    if c:
        contradictions.append(c)

    for check_id, fn in [
        ("deliverable_milestone_registered", _check_deliverable_milestone_citations),
        ("wp_numbering_consistent", _check_wp_numbering),
        ("trl_claim_supported_by_baseline", _check_trl_claim),
    ]:
        passed, c = fn(repo_root, repo_commit)
        checks.append({"check_id": check_id, "passed": passed})
        if c:
            contradictions.append(c)

    passed, guardian_contradictions = _check_guardian_broken_references(source_states, guardian_raw, repo_commit)
    checks.append({"check_id": "guardian_no_broken_references", "passed": passed})
    contradictions.extend(guardian_contradictions)

    passed, stale_contradictions = _check_state_commit_mismatch(source_states, repo_commit)
    checks.append({"check_id": "all_state_fresh_vs_live_head", "passed": passed})
    contradictions.extend(stale_contradictions)

    return checks, contradictions


# ---------------------------------------------------------------------------
# Partner / budget / register / technical readiness
# ---------------------------------------------------------------------------

def _partner_readiness(repo_root, source_states, global_freshness):
    out = []
    for key, path in _partner_files():
        name = key.replace("partner_", "").upper()
        text = _read_repo_file(repo_root, path)
        profile_status = "PROFILE_MISSING" if text is None else "PROFILE_PRESENT"
        missing_fields = []
        pic_status = commitment_status = personnel_status = budget_status = "UNKNOWN"
        role = wp_responsibility = None
        if text is None:
            missing_fields = ["role", "wp_responsibility", "pic_status", "commitment_status",
                              "personnel_status", "budget_status"]
            pic_status = commitment_status = personnel_status = budget_status = "NOT_STARTED"
        out.append({
            "name": name,
            "role": role,
            "wp_responsibility": wp_responsibility,
            "profile_status": profile_status,
            "pic_status": pic_status,
            "commitment_status": commitment_status,
            "personnel_status": personnel_status,
            "budget_status": budget_status,
            "evidence_files": [path] if text is not None else [],
            "missing_fields": missing_fields,
            "freshness": global_freshness if text is not None else "UNAVAILABLE",
            "result": "OK" if text is not None else "FAIL",
        })
    return out


def _budget_readiness(source_states):
    budget = source_states.get("budget", {})
    return {
        "available": budget.get("available", False),
        "reconciled": budget.get("result") == "OK",
        "any_missing": None if not budget.get("available") else budget.get("result") == "WARN",
        "freshness": budget.get("freshness", "UNAVAILABLE"),
        "result": budget.get("result", "FAIL"),
        "source": "budget_state.json",
    }


def _resource_readiness(repo_root, source_states):
    pm_alloc = _file_available(repo_root, "03_implementation/PM_ALLOCATION.md")
    return {
        "pm_allocation": {"available": pm_alloc, "path": "03_implementation/PM_ALLOCATION.md"},
        "budget": _budget_readiness(source_states),
        "equipment": {
            "available": False,
            "note": "No canonical equipment-framework document is declared in config/canonical_documents.json. "
                    "Per-WP equipment-framework filenames are referenced by task descriptions but do not exist "
                    "as canonical files (also flagged by Guardian as broken references).",
        },
    }


def _register_readiness(repo_root):
    def entry(path):
        return {"path": path, "exists_non_empty": _file_available(repo_root, path)}

    return {
        "deliverable_register": entry("02_registers/DELIVERABLE_REGISTER.md"),
        "milestone_register": entry("02_registers/MILESTONE_REGISTER.md"),
        "sequencing_matrix": entry("02_registers/WORK_PLAN_SEQUENCING_MATRIX.md"),
        "dependency_map": entry("03_implementation/DEPENDENCY_MAP.md"),
        "gantt": entry("03_implementation/GANTT.md"),
        "task_register_draft": entry("00_baseline/WP_TASK_REGISTER_DRAFT.md"),
        "master_task_truth_matrix": entry("00_baseline/MASTER_TASK_TRUTH_MATRIX.md"),
        "risk_register": {"path": None, "exists_non_empty": False,
                          "note": "No standalone canonical risk register is declared."},
    }


def _technical_readiness(repo_root):
    def entry(path):
        return {"path": path, "exists_non_empty": _file_available(repo_root, path)}

    return {
        "m1_technical_baseline": entry("00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md"),
        "security_target": entry("docs/technical/FORITECH_SECURITY_TARGET.md"),
        "threat_model": entry("docs/technical/FORITECH_THREAT_MODEL.md"),
        "ip_classification": entry("00_baseline/FORITECH_IP_OUTPUT_CLASSIFICATION.md"),
        "core_contract": entry("docs/technical/FORITECH_CORE_CONTRACT.md"),
        "system_index": entry("00_baseline/FORITECH_SYSTEM_INDEX.md"),
    }


def _guardian_summary(guardian_raw):
    if not guardian_raw:
        return {"available": False, "result": "FAIL", "freshness": "UNAVAILABLE"}
    findings = guardian_raw.get("findings", [])
    by_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    distinct_targets = len({f.get("canonical_issue_key", f.get("id")) for f in findings})
    occurrence_total = sum(f.get("occurrence_count", 1) for f in findings)
    return {
        "available": True,
        "guardian_status": guardian_raw.get("guardian_status"),
        "finding_count": len(findings),
        "distinct_issue_count": distinct_targets,
        "occurrence_total": occurrence_total,
        "by_severity": by_sev,
    }


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def assemble(repo_root: Path, state_dir: Path) -> dict:
    repo_root = Path(repo_root)
    state_dir = Path(state_dir)
    live_commit = get_repo_commit(repo_root)

    source_states = _read_source_states(state_dir, live_commit)
    guardian_raw = read_json_or_none(state_dir / "guardian_state.json")

    cross_document_checks, contradictions = _run_cross_document_checks(
        repo_root, source_states, guardian_raw, live_commit)

    contradictions_by_criterion = {}
    for c in contradictions:
        contradictions_by_criterion.setdefault(c["criterion"], []).append(c)

    # Global freshness: worst across watched producer states.
    global_freshness = _worst_freshness(*(st["freshness"] for st in source_states.values()))

    criterion_evidence = _build_criterion_evidence(
        repo_root, source_states, contradictions_by_criterion, global_freshness)

    missing_evidence = []
    for ce in criterion_evidence:
        for m in ce["missing_evidence"]:
            missing_evidence.append({"criterion": ce["criterion_id"], **m})

    partner_readiness = _partner_readiness(repo_root, source_states, global_freshness)
    budget_readiness = _budget_readiness(source_states)
    resource_readiness = _resource_readiness(repo_root, source_states)
    register_readiness = _register_readiness(repo_root)
    technical_readiness = _technical_readiness(repo_root)
    guardian_summary = _guardian_summary(guardian_raw)

    coverage_summary = {
        "criteria": {ce["criterion_id"]: ce["coverage_ratio"] for ce in criterion_evidence},
        "overall_coverage_ratio": round(
            sum(ce["coverage_ratio"] for ce in criterion_evidence) / len(criterion_evidence), 3
        ) if criterion_evidence else 0.0,
        "documents_required": sum(len(ce["required_evidence"]) for ce in criterion_evidence),
        "documents_available": sum(len(ce["available_evidence"]) for ce in criterion_evidence),
        "documents_missing": sum(len(ce["missing_evidence"]) for ce in criterion_evidence),
        "contradiction_count": len(contradictions),
    }

    findings = []
    for ce in criterion_evidence:
        if ce["evidence_quality"] in ("NONE", "WEAK"):
            findings.append({
                "id": f"evidence-gap-{ce['criterion_id']}",
                "severity": "high" if ce["evidence_quality"] == "NONE" else "medium",
                "title": f"{ce['criterion_id']}: evidence quality {ce['evidence_quality']}",
                "description": f"{len(ce['missing_evidence'])} of {len(ce['required_evidence'])} required "
                               f"evidence item(s) missing.",
                "source": "pipeline.evidence_assembler",
            })
    for c in contradictions:
        findings.append({
            "id": c["id"], "severity": c["severity"], "title": c["reason"],
            "description": c["claim"], "source": c["claim_source"],
        })

    overall_result = _worst_result(
        *(ce["result"] for ce in criterion_evidence),
        *(st["result"] for st in source_states.values()),
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "evidence_model_version": EVIDENCE_MODEL_VERSION,
        "component_id": "evidence_assembler",
        "repo_commit": live_commit,
        "run_timestamp": now_iso(),
        "freshness": global_freshness,
        "result": overall_result,
        "source_states": source_states,
        "criterion_evidence": criterion_evidence,
        "cross_document_checks": cross_document_checks,
        "contradictions": contradictions,
        "missing_evidence": missing_evidence,
        "guardian_summary": guardian_summary,
        "partner_readiness": partner_readiness,
        "budget_readiness": budget_readiness,
        "resource_readiness": resource_readiness,
        "register_readiness": register_readiness,
        "technical_readiness": technical_readiness,
        "coverage_summary": coverage_summary,
        "findings": findings,
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    import os
    import sys
    repo_root = os.environ.get("FORISEC_REPO_ROOT")
    state_dir = os.environ.get("FORISEC_STATE_DIR")
    if not repo_root or not state_dir:
        print("[evidence_assembler] CONFIG ERROR: FORISEC_REPO_ROOT and FORISEC_STATE_DIR must be set.",
              file=sys.stderr)
        sys.exit(1)
    repo_root_p = Path(repo_root).expanduser().resolve()
    state_dir_p = Path(state_dir).expanduser().resolve()
    if not repo_root_p.exists():
        print(f"[evidence_assembler] CONFIG ERROR: FORISEC_REPO_ROOT does not exist: {repo_root_p}", file=sys.stderr)
        sys.exit(1)
    state_dir_p.mkdir(parents=True, exist_ok=True)
    result = assemble(repo_root_p, state_dir_p)
    print(f"[evidence_assembler] completed -- result={result['result']} freshness={result['freshness']} "
          f"-- state written to {state_dir_p}")


if __name__ == "__main__":
    main()
