"""
Agent 5 (proposal_intelligence) tests.

STEP 2 OF 2: the primary diagnostic score is evidence-gated, computed
from proposal_evidence_state.json -- never from keyword/word-count
signals. The old keyword scorer (_score_criterion) still exists but
only feeds the secondary text_completeness_score metric.
"""
import json
import subprocess
from pathlib import Path

from agents import proposal_intelligence as pi
from agents.proposal_intelligence import (
    CRITERIA, CRITERION_ORDER, _score_criterion, _score_from_evidence,
    _competitive_assessment, _fundability_label, EVIDENCE_CEILING, RESULT_CEILING,
)


def _make_repo(tmp_path, excellence="", impact="", implementation=""):
    repo = tmp_path / "repo"
    (repo / "04_proposal").mkdir(parents=True)
    (repo / "04_proposal" / "EXCELLENCE.md").write_text(excellence)
    (repo / "04_proposal" / "IMPACT.md").write_text(impact)
    (repo / "04_proposal" / "IMPLEMENTATION.md").write_text(implementation)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _repo_commit(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True).stdout.strip()


def _default_ce(criterion_id, coverage_ratio=1.0, quality="STRONG", result="OK",
                 freshness="FRESH", contradictions=None, missing_evidence=None, supporting_sources=None):
    return {
        "criterion_id": criterion_id, "coverage_ratio": coverage_ratio, "evidence_quality": quality,
        "result": result, "freshness": freshness, "contradictions": contradictions or [],
        "missing_evidence": missing_evidence or [], "supporting_sources": supporting_sources or [],
        "required_evidence": [], "available_evidence": [],
    }


def _write_evidence_bundle(state_dir, repo_commit, criterion_overrides=None, contradictions=None,
                            partner_readiness=None, guardian_summary=None, technical_readiness=None,
                            result="OK", freshness="FRESH"):
    criterion_overrides = criterion_overrides or {}
    ce_list = []
    for cid in CRITERION_ORDER:
        base = _default_ce(cid)
        base.update(criterion_overrides.get(cid, {}))
        ce_list.append(base)
    bundle = {
        "schema_version": "1.0", "evidence_model_version": "1.0", "repo_commit": repo_commit,
        "run_timestamp": "x", "freshness": freshness, "result": result,
        "source_states": {}, "criterion_evidence": ce_list, "cross_document_checks": [],
        "contradictions": contradictions or [], "missing_evidence": [],
        "guardian_summary": guardian_summary or {
            "available": True, "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            "finding_count": 0, "distinct_issue_count": 0,
        },
        "partner_readiness": partner_readiness if partner_readiness is not None else [
            {"name": n, "role": None, "wp_responsibility": None, "profile_status": "PROFILE_PRESENT",
             "pic_status": "CONFIRMED", "commitment_status": "CONFIRMED", "personnel_status": "CONFIRMED",
             "budget_status": "CONFIRMED", "evidence_files": [f"05_partners/{n}.md"], "missing_fields": [],
             "freshness": "FRESH", "result": "OK"}
            for n in ["FORITECH", "RTU", "LOGIICDEV", "SOLARIX"]
        ],
        "budget_readiness": {"available": True, "reconciled": True, "result": "OK", "freshness": "FRESH"},
        "resource_readiness": {}, "register_readiness": {},
        "technical_readiness": technical_readiness if technical_readiness is not None else {
            k: {"path": f"x/{k}.md", "exists_non_empty": True} for k in
            ["m1_technical_baseline", "security_target", "threat_model", "ip_classification",
             "core_contract", "system_index"]
        },
        "coverage_summary": {}, "findings": [],
    }
    (state_dir / "proposal_evidence_state.json").write_text(json.dumps(bundle))
    return bundle


# ── Secondary metric: text-completeness (old keyword/word-count scorer) ──

def test_text_completeness_score_boundaries_0_to_5(tmp_path):
    result = _score_criterion("E1", CRITERIA["E1"], None, None, False)
    assert result["score"] == 0.0
    assert 0 <= result["score"] <= result["max_score"]


def test_text_completeness_score_max_is_five_with_all_signals(tmp_path):
    text = ("objective state of the art TRL 7 achieves 99% accuracy " * 200)
    result = _score_criterion("E1", CRITERIA["E1"], text, "FROZEN", False)
    assert result["score"] == 5.0


def test_text_completeness_half_point_step_only(tmp_path):
    text = "objective " * 50
    result = _score_criterion("E1", CRITERIA["E1"], text, "DRAFT", False)
    assert (result["score"] * 2) == int(result["score"] * 2)


def test_text_completeness_missing_evidence_does_not_increase_score(tmp_path):
    text_no_signals = "lorem ipsum dolor sit amet " * 50
    result = _score_criterion("E1", CRITERIA["E1"], text_no_signals, "FROZEN", False)
    assert result["score"] == 0.0
    assert len(result["missing_evidence"]) > 0


def test_text_completeness_unsupported_claims_absent_from_evidence(tmp_path):
    text = "This proposal is the best in the world and guaranteed to win."
    result = _score_criterion("E1", CRITERIA["E1"], text, "FROZEN", False)
    bases = [e["basis"] for e in result["evidence"]]
    assert "This proposal is the best in the world" not in bases
    assert result["score"] == 0.0


def test_text_completeness_is_secondary_and_clearly_labeled(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective state of the art TRL 7 99% " * 100,
        impact="outcome dissemination exploitation KPI " * 100,
        implementation="work plan consortium budget partner " * 100,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # No evidence bundle -> diagnostic (primary) score must be 0, while the
    # secondary text-completeness metric is still computed from the text.
    result = pi.run(repo, state_dir)
    assert result["diagnostic_score"]["total"] == 0.0
    assert result["text_completeness_score"]["total"] > 0.0
    assert result["text_completeness_score"]["label"] == "STRUCTURAL_TEXT_COMPLETENESS"
    assert result["text_completeness_score"]["warning"] == "NOT_EVALUATOR_SCORE"


# ── Primary: evidence-gated diagnostic score ──────────────────────────────

def test_diagnostic_score_reads_evidence_bundle_not_text(tmp_path):
    # Deliberately empty/irrelevant proposal text -- if the primary score
    # were still text-derived, this would score near zero everywhere.
    repo = _make_repo(tmp_path, excellence="xyz", impact="xyz", implementation="xyz")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)  # full STRONG/1.0 coverage bundle
    result = pi.run(repo, state_dir)
    ds = result["diagnostic_score"]
    # STRONG ceiling = 4.5 for every criterion regardless of the text content.
    assert ds["excellence"] == 4.5
    assert ds["impact"] == 4.5
    assert ds["implementation"] == 4.5
    assert ds["total"] == 13.5


def test_diagnostic_score_ignores_word_count(tmp_path):
    repo = _make_repo(tmp_path, excellence="short", impact="short", implementation="short")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    result = pi.run(repo, state_dir)
    # 5 words total is far below any min_word_count threshold, yet the
    # evidence-gated score is unaffected because it never reads word count.
    assert result["diagnostic_score"]["total"] == 13.5


def test_diagnostic_score_ignores_keyword_hits(tmp_path):
    # None of the CRITERIA regex signals match this text at all.
    no_keywords = "qqqqqq zzzzzz wwwwww " * 100
    repo = _make_repo(tmp_path, excellence=no_keywords, impact=no_keywords, implementation=no_keywords)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    result = pi.run(repo, state_dir)
    assert result["diagnostic_score"]["total"] == 13.5  # unaffected by zero keyword hits
    # Meanwhile the secondary text-completeness metric DOES reflect the
    # absence of keywords -- proving the two are genuinely independent.
    assert result["text_completeness_score"]["total"] == 0.0


def test_evidence_ceiling_matrix(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    for quality, ceiling in EVIDENCE_CEILING.items():
        ce = _default_ce("E1", coverage_ratio=1.0, quality=quality)  # base_score would be 5.0
        section = _score_from_evidence("E1", ce, {})
        assert section["score"] == ceiling, f"{quality} should cap at {ceiling}, got {section['score']}"
        assert section["evidence_ceiling"] == ceiling


def test_contradiction_penalties_by_severity(tmp_path):
    for severity, expected_penalty in [("critical", 1.5), ("high", 1.0), ("medium", 0.5), ("low", 0.25)]:
        contradictions_by_id = {"c1": {"id": "c1", "severity": severity}}
        ce = _default_ce("E1", coverage_ratio=1.0, quality="STRONG", contradictions=["c1"])
        section = _score_from_evidence("E1", ce, contradictions_by_id)
        assert section["contradiction_penalty"] == expected_penalty
        # rounded to nearest 0.5 with the same round(x*2)/2 convention as
        # production (ties round-half-to-even) -- never hardcode a
        # round-half-up assumption here.
        expected_score = round((4.5 - expected_penalty) * 2) / 2
        assert section["score"] == expected_score


def test_contradiction_penalty_capped_at_three(tmp_path):
    # 3 criticals would be 4.5 raw penalty -- must cap at 3.0.
    contradictions_by_id = {f"c{i}": {"id": f"c{i}", "severity": "critical"} for i in range(3)}
    ce = _default_ce("E1", coverage_ratio=1.0, quality="STRONG", contradictions=list(contradictions_by_id))
    section = _score_from_evidence("E1", ce, contradictions_by_id)
    assert section["contradiction_penalty"] == 3.0
    assert section["score"] == 1.5  # 4.5 - 3.0


def test_result_ceiling_matrix(tmp_path):
    for result_value, ceiling in [("CRITICAL", 2.0), ("FAIL", 2.5), ("WARN", 3.0), ("REVIEW", 3.5)]:
        ce = _default_ce("E1", coverage_ratio=1.0, quality="STRONG", result=result_value)  # ceiling would be 4.5
        section = _score_from_evidence("E1", ce, {})
        assert section["score"] == ceiling
        assert section["result_ceiling"] == ceiling
    ce_ok = _default_ce("E1", coverage_ratio=1.0, quality="STRONG", result="OK")
    section_ok = _score_from_evidence("E1", ce_ok, {})
    assert section_ok["result_ceiling"] is None
    assert section_ok["score"] == 4.5


def test_score_rounds_to_nearest_half(tmp_path):
    # coverage 0.67 -> base_score 3.35 -> nearest 0.5 is 3.5
    ce = _default_ce("E1", coverage_ratio=0.67, quality="SUFFICIENT")  # ceiling 4.0, doesn't bind
    section = _score_from_evidence("E1", ce, {})
    assert (section["score"] * 2) == int(section["score"] * 2)
    assert section["score"] == 3.5


def test_im2im3_cannot_be_5_with_empty_partner_profiles(tmp_path):
    # Empty partner profiles -> low coverage_ratio + WEAK quality, exactly
    # as pipeline.evidence_assembler reports for the real repo.
    ce = _default_ce("IM2IM3", coverage_ratio=0.333, quality="WEAK", result="WARN")
    section = _score_from_evidence("IM2IM3", ce, {})
    assert section["score"] <= EVIDENCE_CEILING["WEAK"]
    assert section["score"] < 5.0


def test_missing_pm_allocation_limits_im2im3(tmp_path):
    contradictions_by_id = {"contradiction-pm-claim-no-allocation": {"severity": "medium"}}
    ce = _default_ce("IM2IM3", coverage_ratio=0.7, quality="PARTIAL",
                      contradictions=["contradiction-pm-claim-no-allocation"])
    section = _score_from_evidence("IM2IM3", ce, contradictions_by_id)
    assert section["contradiction_penalty"] == 0.5
    assert section["score"] <= EVIDENCE_CEILING["PARTIAL"] - 0.5


def test_missing_budget_state_limits_im2im3(tmp_path):
    contradictions_by_id = {"contradiction-budget-not-reconciled": {"severity": "high"}}
    ce = _default_ce("IM2IM3", coverage_ratio=0.7, quality="PARTIAL",
                      contradictions=["contradiction-budget-not-reconciled"])
    section = _score_from_evidence("IM2IM3", ce, contradictions_by_id)
    assert section["contradiction_penalty"] == 1.0
    assert section["score"] <= EVIDENCE_CEILING["PARTIAL"] - 1.0


def test_evidence_bundle_unavailable_blocks_everything(tmp_path):
    repo = _make_repo(tmp_path, excellence="objective " * 200, impact="outcome " * 200,
                       implementation="work plan " * 200)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = pi.run(repo, state_dir)  # no proposal_evidence_state.json written
    assert result["evidence_bundle_available"] is False
    assert result["evidence_bundle_freshness"] == "UNAVAILABLE"
    assert result["diagnostic_score"]["total"] == 0.0
    assert result["overall_status"] == "EVIDENCE_UNAVAILABLE"
    assert result["fundability"] == "BLOCKED"
    assert result["promotion_status"] == "BLOCKED"


def test_evidence_bundle_stale_is_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_evidence_bundle(state_dir, "some-other-commit")  # deliberately not the live HEAD
    result = pi.run(repo, state_dir)
    assert result["evidence_bundle_available"] is True
    assert result["evidence_bundle_freshness"] == "STALE"
    assert any("STALE" in f["title"] for f in result["findings"])


def test_critical_evidence_result_blocks_overall(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit, result="CRITICAL")
    result = pi.run(repo, state_dir)
    assert result["overall_status"] == "BLOCKED"
    assert result["fundability"] == "BLOCKED"
    assert result["promotion_status"] == "BLOCKED"


# ── Promotion / blocking rules (with a clean evidence bundle so the
#    non-evidence blockers -- Guardian, Supervisor -- can be isolated) ────

def test_critical_finding_blocks_promotion(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    (state_dir / "guardian_state.json").write_text(json.dumps({
        "findings": [{"severity": "critical", "title": "x"}]
    }))
    result = pi.run(repo, state_dir)
    assert result["promotion_status"] == "BLOCKED"
    assert result["fundability"] == "BLOCKED"


def test_degraded_supervisor_blocks_promotion(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    (state_dir / "supervisor_state.json").write_text(json.dumps({"overall_status": "DEGRADED"}))
    result = pi.run(repo, state_dir)
    assert result["promotion_status"] == "BLOCKED"


def test_canonical_score_never_auto_promoted(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)  # clean bundle -> not blocked
    result = pi.run(repo, state_dir)
    assert result["canonical_score"] is None
    assert result["promotion_status"] != "APPROVED"

    data = json.loads((state_dir / "proposal_intelligence_state.json").read_text())
    data["canonical_score"] = 12.5
    data["promotion_status"] = "APPROVED"
    (state_dir / "proposal_intelligence_state.json").write_text(json.dumps(data))

    result2 = pi.run(repo, state_dir)
    assert result2["canonical_score"] == 12.5
    assert result2["promotion_status"] == "APPROVED"


def test_human_approval_overridden_by_new_critical_finding(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    pi.run(repo, state_dir)
    data = json.loads((state_dir / "proposal_intelligence_state.json").read_text())
    data["canonical_score"] = 11.0
    data["promotion_status"] = "APPROVED"
    (state_dir / "proposal_intelligence_state.json").write_text(json.dumps(data))

    (state_dir / "guardian_state.json").write_text(json.dumps({
        "findings": [{"severity": "critical", "title": "new break"}]
    }))
    result = pi.run(repo, state_dir)
    assert result["promotion_status"] == "BLOCKED"
    assert result["canonical_score"] == 11.0


# ── Fundability label -- never "FUNDABLE" ─────────────────────────────────

def test_fundability_labels_boundaries():
    assert _fundability_label(9.9, False) == "NOT READY"
    assert _fundability_label(10.0, False) == "BORDERLINE"
    assert _fundability_label(12.0, False) == "COMPETITIVE"
    assert _fundability_label(13.5, False) == "STRONG"
    assert _fundability_label(14.0, True) == "BLOCKED"


def test_fundability_never_returns_fundable():
    for total in [0, 5, 9.9, 10, 12, 13.5, 15]:
        for blocked in (True, False):
            assert _fundability_label(total, blocked) != "FUNDABLE"


# ── Competitive score ──────────────────────────────────────────────────────

def test_competitive_score_formula_is_average_of_five_components():
    section_scores = [{"criterion_id": c, "confidence": 0.8, "coverage_ratio": 0.8, "supporting_sources": []}
                      for c in CRITERION_ORDER]
    bundle = {"partner_readiness": [], "guardian_summary": {}, "contradictions": [],
              "technical_readiness": {}}
    ca = _competitive_assessment(bundle, section_scores)
    components = ca["components"]
    avg = sum(c["score"] for c in components.values()) / len(components)
    assert round(avg * 5, 2) == ca["score"]


def test_competitive_labels_valid():
    section_scores = [{"criterion_id": c, "confidence": 0.5, "coverage_ratio": 0.5, "supporting_sources": []}
                      for c in CRITERION_ORDER]
    bundle = {"partner_readiness": [], "guardian_summary": {}, "contradictions": [], "technical_readiness": {}}
    ca = _competitive_assessment(bundle, section_scores)
    assert ca["label"] in ("NOT COMPETITIVE", "WEAK", "COMPETITIVE BUT WEAK", "STRONG", "TOP TIER")


def test_competitive_differentiation_not_keyword_based():
    """Differentiation must be derived from concrete document existence,
    never from a fixed keyword list matched against prose."""
    section_scores = [{"criterion_id": c, "confidence": 0.5, "coverage_ratio": 0.0,
                       "supporting_sources": []} for c in CRITERION_ORDER]
    bundle_no_docs = {"partner_readiness": [], "guardian_summary": {}, "contradictions": [],
                      "technical_readiness": {k: {"exists_non_empty": False} for k in
                                              ["m1_technical_baseline", "security_target", "threat_model",
                                               "ip_classification", "core_contract"]}}
    ca_low = _competitive_assessment(bundle_no_docs, section_scores)
    assert ca_low["components"]["differentiation"]["score"] == 0.0

    bundle_with_docs = dict(bundle_no_docs)
    bundle_with_docs["technical_readiness"] = {k: {"exists_non_empty": True} for k in
                                               ["m1_technical_baseline", "security_target", "threat_model",
                                                "ip_classification", "core_contract"]}
    ca_high = _competitive_assessment(bundle_with_docs, section_scores)
    assert ca_high["components"]["differentiation"]["score"] > ca_low["components"]["differentiation"]["score"]
    assert "keyword" not in ca_high["components"]["differentiation"]["rationale"].lower() or \
           "not keyword matching" in ca_high["components"]["differentiation"]["rationale"].lower()


def test_consortium_credibility_never_fabricates_partner_facts():
    partners = [{"name": "X", "profile_status": "PROFILE_MISSING", "pic_status": "UNKNOWN",
                "commitment_status": "UNKNOWN", "personnel_status": "UNKNOWN", "budget_status": "NOT_STARTED"}]
    bundle = {"partner_readiness": partners, "guardian_summary": {}, "contradictions": [], "technical_readiness": {}}
    section_scores = [{"criterion_id": c, "confidence": 0.5, "coverage_ratio": 0.5, "supporting_sources": []}
                      for c in CRITERION_ORDER]
    ca = _competitive_assessment(bundle, section_scores)
    assert ca["components"]["consortium_credibility"]["score"] == 0.0


# ── Schema / snapshot / history ────────────────────────────────────────────

def test_state_validates_against_schema(tmp_path):
    import jsonschema
    repo = _make_repo(tmp_path, excellence="objective " * 100)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    commit = _repo_commit(repo)
    _write_evidence_bundle(state_dir, commit)
    result = pi.run(repo, state_dir)
    project_root = Path(__file__).resolve().parents[1]
    schema = json.loads((project_root / "contracts" / "proposal_intelligence_state.schema.json").read_text())
    jsonschema.validate(result, schema)


def test_snapshot_written_once_per_commit(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pi.run(repo, state_dir)
    pi.run(repo, state_dir)
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 1


def test_second_run_same_commit_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    r1 = pi.run(repo, state_dir)
    r2 = pi.run(repo, state_dir)
    assert r1["diagnostic_score"] == r2["diagnostic_score"]
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 1


def test_force_flag_creates_new_snapshot(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pi.run(repo, state_dir)
    pi.run(repo, state_dir, force_snapshot=True)
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 2


def test_proposal_repo_unchanged_after_run(tmp_path):
    repo = _make_repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    pi.run(repo, state_dir)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after == ""


# ── Timeline: total_gain must ignore MODEL_RECALCULATION ─────────────────

def test_timeline_gain_ignores_model_recalculation(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pi._write_history_snapshot(state_dir, "commitA", {
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "commitA", "scoring_model_version": "1.1",
        "total": 14.5, "excellence": 4.5, "impact": 5.0, "implementation": 5.0, "competitive_score": 1.8,
        "critical_finding_count": 8, "promotion_status": "BLOCKED", "event_type": "REPOSITORY_CHANGE",
    }, force=True)
    pi._write_history_snapshot(state_dir, "commitA", {
        "timestamp": "2026-01-02T00:00:00", "repo_commit": "commitA", "scoring_model_version": "2.0",
        "total": 6.5, "excellence": 2.0, "impact": 4.5, "implementation": 0.0, "competitive_score": 2.65,
        "critical_finding_count": 8, "promotion_status": "BLOCKED", "event_type": "MODEL_RECALCULATION",
    }, force=True)
    pi._write_history_snapshot(state_dir, "commitB", {
        "timestamp": "2026-01-03T00:00:00", "repo_commit": "commitB", "scoring_model_version": "2.0",
        "total": 9.5, "excellence": 3.0, "impact": 4.5, "implementation": 2.0, "competitive_score": 3.0,
        "critical_finding_count": 0, "promotion_status": "PENDING_REVIEW", "event_type": "REPOSITORY_CHANGE",
    }, force=True)

    summary = pi._build_timeline_summary(state_dir)
    assert summary["snapshot_count"] == 3
    assert summary["repository_change_count"] == 2
    assert summary["model_recalculation_count"] == 1
    # 14.5 (commitA, REPOSITORY_CHANGE) -> 9.5 (commitB, REPOSITORY_CHANGE) = -5.0
    # the intermediate MODEL_RECALCULATION snapshot's 6.5 must never be used.
    assert summary["total_gain"] == -5.0
