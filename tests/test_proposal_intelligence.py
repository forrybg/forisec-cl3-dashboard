"""
Phase 3 — Agent 5 (proposal_intelligence) scoring/formula/honesty tests.
"""
import json
import subprocess

from agents import proposal_intelligence as pi
from agents.proposal_intelligence import CRITERIA, _score_criterion, _competitive_assessment, _fundability_label


def _make_repo(tmp_path, excellence="", impact="", implementation="", partners=None):
    repo = tmp_path / "repo"
    (repo / "04_proposal").mkdir(parents=True)
    if partners:
        (repo / "05_partners").mkdir()
    (repo / "04_proposal" / "EXCELLENCE.md").write_text(excellence)
    (repo / "04_proposal" / "IMPACT.md").write_text(impact)
    (repo / "04_proposal" / "IMPLEMENTATION.md").write_text(implementation)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# ── Scoring formula / boundaries ─────────────────────────────────────────

def test_score_boundaries_0_to_5(tmp_path):
    result = _score_criterion("E1", CRITERIA["E1"], None, None, False)
    assert result["score"] == 0.0
    assert 0 <= result["score"] <= result["max_score"]


def test_score_max_is_five_with_all_signals(tmp_path):
    text = ("objective state of the art TRL 7 achieves 99% accuracy " * 200)
    result = _score_criterion("E1", CRITERIA["E1"], text, "FROZEN", False)
    assert result["score"] <= 5.0
    assert result["score"] == 5.0  # all 4 signals + word count signal, capped at 5


def test_half_point_step_only(tmp_path):
    text = "objective " * 50  # only 1 of 4 signals, short doc
    result = _score_criterion("E1", CRITERIA["E1"], text, "DRAFT", False)
    assert (result["score"] * 2) == int(result["score"] * 2)  # always a multiple of 0.5


def test_total_is_excellence_plus_impact_plus_implementation(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective state of the art TRL 7 99% " * 100,
        impact="outcome dissemination exploitation KPI " * 100,
        implementation="work plan consortium budget partner " * 100,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = pi.run(repo, state_dir)
    ds = result["diagnostic_score"]
    assert round(ds["excellence"] + ds["impact"] + ds["implementation"], 2) == ds["total"]
    assert 0 <= ds["total"] <= 15


def test_missing_document_scores_zero_not_a_guess(tmp_path):
    repo = _make_repo(tmp_path)  # all three files empty
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = pi.run(repo, state_dir)
    assert result["diagnostic_score"]["total"] == 0.0
    for s in result["section_scores"]:
        assert s["score"] == 0.0
        assert "document exists" in s["missing_evidence"] or s["missing_evidence"]


def test_missing_evidence_does_not_increase_score(tmp_path):
    text_no_signals = "lorem ipsum dolor sit amet " * 50
    result = _score_criterion("E1", CRITERIA["E1"], text_no_signals, "FROZEN", False)
    assert result["score"] == 0.0
    assert len(result["missing_evidence"]) > 0


# ── Promotion / blocking rules ────────────────────────────────────────────

def test_critical_finding_blocks_promotion(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective " * 50, impact="outcome " * 50, implementation="work plan " * 50,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "guardian_state.json").write_text(json.dumps({
        "findings": [{"severity": "critical", "title": "x"}]
    }))
    result = pi.run(repo, state_dir)
    assert result["promotion_status"] == "BLOCKED"
    assert result["fundability"] == "BLOCKED"


def test_degraded_supervisor_blocks_promotion(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective " * 50, impact="outcome " * 50, implementation="work plan " * 50,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "supervisor_state.json").write_text(json.dumps({"overall_status": "DEGRADED"}))
    result = pi.run(repo, state_dir)
    assert result["promotion_status"] == "BLOCKED"


def test_canonical_score_never_auto_promoted(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective state of the art TRL 7 99% " * 100,
        impact="outcome dissemination exploitation " * 100,
        implementation="work plan consortium budget " * 100,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = pi.run(repo, state_dir)
    assert result["canonical_score"] is None  # nothing approved yet -> stays null
    assert result["promotion_status"] != "APPROVED"  # agent itself never sets APPROVED

    # Simulate a human manually approving in the state file...
    data = json.loads((state_dir / "proposal_intelligence_state.json").read_text())
    data["canonical_score"] = 12.5
    data["promotion_status"] = "APPROVED"
    (state_dir / "proposal_intelligence_state.json").write_text(json.dumps(data))

    # ...re-running the agent must carry the value forward, not invent a new one.
    result2 = pi.run(repo, state_dir)
    assert result2["canonical_score"] == 12.5
    assert result2["promotion_status"] == "APPROVED"


def test_human_approval_overridden_by_new_critical_finding(tmp_path):
    repo = _make_repo(
        tmp_path,
        excellence="objective " * 50, impact="outcome " * 50, implementation="work plan " * 50,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
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
    assert result["canonical_score"] == 11.0  # value preserved, only status changes


# ── Competitive score ──────────────────────────────────────────────────────

def test_competitive_score_formula_is_average_of_five_components():
    section_scores = [
        {"criterion_id": c, "score": 4.0, "confidence": 0.8, "evidence": []}
        for c in ["E1", "E2", "I1", "I2I3", "IM1", "IM2IM3"]
    ]
    ca = _competitive_assessment(section_scores, None, None)
    components = ca["components"]
    avg = sum(c["score"] for c in components.values()) / len(components)
    assert round(avg * 5, 2) == ca["score"]


def test_competitive_labels_boundaries():
    assert _fundability_label(9.9, False, "PENDING_REVIEW") == "NOT READY"
    assert _fundability_label(10.0, False, "PENDING_REVIEW") == "BORDERLINE"
    assert _fundability_label(12.0, False, "PENDING_REVIEW") == "COMPETITIVE"
    assert _fundability_label(13.5, False, "PENDING_REVIEW") == "STRONG"
    assert _fundability_label(14.0, True, "PENDING_REVIEW") == "BLOCKED"
    assert _fundability_label(14.0, False, "BLOCKED") == "BLOCKED"


def test_competitive_score_never_claims_real_market_position():
    section_scores = [{"criterion_id": c, "score": 3.0, "confidence": 0.5, "evidence": []}
                       for c in CRITERIA]
    ca = _competitive_assessment(section_scores, None, None)
    assert ca["label"] in ("NOT COMPETITIVE", "WEAK", "COMPETITIVE BUT WEAK", "STRONG", "TOP TIER")


def test_unsupported_claims_absent_from_evidence(tmp_path):
    text = "This proposal is the best in the world and guaranteed to win."
    result = _score_criterion("E1", CRITERIA["E1"], text, "FROZEN", False)
    # None of our deterministic signals match vague marketing language --
    # it must not be treated as evidence.
    bases = [e["basis"] for e in result["evidence"]]
    assert "This proposal is the best in the world" not in bases
    assert result["score"] == 0.0


# ── Schema / snapshot / history ────────────────────────────────────────────

def test_state_validates_against_schema(tmp_path):
    import jsonschema
    from pathlib import Path
    repo = _make_repo(
        tmp_path,
        excellence="objective state of the art TRL 7 99% " * 100,
        impact="outcome dissemination exploitation " * 100,
        implementation="work plan consortium budget " * 100,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = pi.run(repo, state_dir)
    project_root = Path(__file__).resolve().parents[1]
    schema = json.loads((project_root / "contracts" / "proposal_intelligence_state.schema.json").read_text())
    jsonschema.validate(result, schema)


def test_snapshot_written_once_per_commit(tmp_path):
    repo = _make_repo(tmp_path, excellence="objective " * 50)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pi.run(repo, state_dir)
    pi.run(repo, state_dir)
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 1


def test_second_run_same_commit_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path, excellence="objective " * 50)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    r1 = pi.run(repo, state_dir)
    r2 = pi.run(repo, state_dir)
    assert r1["diagnostic_score"] == r2["diagnostic_score"]
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 1


def test_force_flag_creates_new_snapshot(tmp_path):
    repo = _make_repo(tmp_path, excellence="objective " * 50)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pi.run(repo, state_dir)
    pi.run(repo, state_dir, force_snapshot=True)
    history_files = list((state_dir / "history").glob("evaluation_*.json"))
    assert len(history_files) == 2


def test_proposal_repo_unchanged_after_run(tmp_path):
    import subprocess as sp
    repo = _make_repo(tmp_path, excellence="objective " * 50)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    before = sp.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    pi.run(repo, state_dir)
    after = sp.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after == ""
