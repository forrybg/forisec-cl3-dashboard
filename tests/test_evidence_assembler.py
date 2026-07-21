"""
pipeline/evidence_assembler.py tests -- STEP 1 OF 2 (connect FORISEC
proposal evidence). Builds a minimal, self-contained fake proposal repo
per test (never touches the real forisec-cl3-2026 repo).
"""
import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from pipeline import evidence_assembler as ea

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"


def _git_init(repo: Path):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _full_repo(tmp_path, extra_files=None, empty_partners=True, skip_files=None):
    """A repo with every canonical path the assembler looks for, all
    non-empty by default, so tests can flip exactly the thing they're
    checking."""
    repo = tmp_path / "repo"
    skip_files = skip_files or set()

    files = {
        "04_proposal/EXCELLENCE.md": "Objectives. TRL 5. State of the art.",
        "04_proposal/IMPACT.md": "Outcomes and impact pathway.",
        "04_proposal/IMPLEMENTATION.md": "Work plan, WP1 WP2 WP3 WP4 WP5 WP6, consortium, partner, budget, person-month PM.",
        "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md": "Starting TRL 5 baseline evidence.",
        "docs/technical/FORITECH_SECURITY_TARGET.md": "Security target content.",
        "docs/technical/FORITECH_THREAT_MODEL.md": "Threat model content.",
        "00_baseline/MASTER_TASK_TRUTH_MATRIX.md": "Task truth matrix content.",
        "02_registers/MILESTONE_REGISTER.md": "M1 milestone.",
        "docs/business/FORITECH_BUSINESS_MODEL.md": "Business model content.",
        "00_baseline/FORITECH_IP_OUTPUT_CLASSIFICATION.md": "IP classification content.",
        "01_work_packages/WP1_PROJECT_MANAGEMENT.md": "WP1 description.",
        "01_work_packages/WP2_PQC_PLATFORM.md": "WP2 description.",
        "01_work_packages/WP3_EMBEDDED_PROVENANCE.md": "WP3 description.",
        "01_work_packages/WP4_FPGA_HARDWARE_SECURITY.md": "WP4 description.",
        "01_work_packages/WP5_OPERATIONAL_PILOT.md": "WP5 description.",
        "01_work_packages/WP6_EXPLOITATION_STANDARDISATION.md": "WP6 description.",
        "00_baseline/WP_TASK_REGISTER_DRAFT.md": "Task register content.",
        "02_registers/DELIVERABLE_REGISTER.md": "Deliverable register content.",
        "03_implementation/DEPENDENCY_MAP.md": "Dependency map content.",
        "02_registers/WORK_PLAN_SEQUENCING_MATRIX.md": "Sequencing matrix content.",
        "03_implementation/GANTT.md": "Gantt content.",
        "00_baseline/CONSORTIUM_AND_WP_STRUCTURE.md": "Consortium structure content.",
        "03_implementation/PM_ALLOCATION.md": "PM allocation content.",
    }
    partner_content = "" if empty_partners else "Partner profile content."
    for p in ["05_partners/FORITECH.md", "05_partners/RTU.md", "05_partners/LOGIICDEV.md", "05_partners/SOLARIX.md"]:
        files[p] = partner_content

    if extra_files:
        files.update(extra_files)

    for rel, content in files.items():
        if rel in skip_files:
            continue
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    _git_init(repo)
    return repo


def _state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


def _write_state(state_dir, filename, data):
    (state_dir / filename).write_text(json.dumps(data))


# ── Reads all declared source states ─────────────────────────────────────

def test_reads_all_declared_source_states(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    assert set(result["source_states"].keys()) == set(ea.SOURCE_STATE_FILES.keys())
    for name in ea.SOURCE_STATE_FILES:
        assert result["source_states"][name]["available"] is False  # nothing written yet


# ── Budget state is actually consumed ────────────────────────────────────

def test_budget_state_is_consumed(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    import subprocess as sp
    commit = sp.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
    _write_state(state_dir, "budget_state.json", {
        "repo_commit": commit, "available": True, "any_missing": False, "total_pm": 100, "total_eur": 1000,
    })
    result = ea.assemble(repo, state_dir)
    assert result["source_states"]["budget"]["available"] is True
    assert result["budget_readiness"]["reconciled"] is True
    im2im3 = next(c for c in result["criterion_evidence"] if c["criterion_id"] == "IM2IM3")
    assert any(e["key"] == "budget_state" for e in im2im3["available_evidence"])


def test_missing_budget_limits_im2im3_evidence_quality(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)  # no budget_state.json written at all
    result = ea.assemble(repo, state_dir)
    im2im3 = next(c for c in result["criterion_evidence"] if c["criterion_id"] == "IM2IM3")
    assert any(m["key"] == "budget_state" for m in im2im3["missing_evidence"])
    assert im2im3["evidence_quality"] in ("NONE", "WEAK", "PARTIAL")
    assert "budget" in ea._read_repo_file(repo, "04_proposal/IMPLEMENTATION.md").lower()
    # the word "budget" is present in the narrative, but coverage is still
    # penalized because budget_state.json itself is unavailable
    passed, contradiction = ea._check_budget_reconciled(repo, result["source_states"], result["repo_commit"])
    assert passed is False
    assert contradiction is not None


# ── Empty partner files => missing evidence, never a fabricated fact ────

def test_empty_partner_files_produce_missing_evidence(tmp_path):
    repo = _full_repo(tmp_path, empty_partners=True)
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    for p in result["partner_readiness"]:
        assert p["profile_status"] == "PROFILE_MISSING"
        assert p["result"] == "FAIL"
        assert p["evidence_files"] == []
        assert "role" in p["missing_fields"]
    im2im3 = next(c for c in result["criterion_evidence"] if c["criterion_id"] == "IM2IM3")
    partner_missing = [m for m in im2im3["missing_evidence"] if m["key"].startswith("partner_")]
    assert len(partner_missing) == 4


def test_non_empty_partner_file_is_present_not_fabricated(tmp_path):
    repo = _full_repo(tmp_path, empty_partners=False)
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    for p in result["partner_readiness"]:
        assert p["profile_status"] == "PROFILE_PRESENT"
        assert p["result"] == "OK"
        # This module never invents partner facts -- role/PIC/commitment
        # stay UNKNOWN unless a real parser for profile fields exists.
        assert p["pic_status"] == "UNKNOWN"


# ── Canonical registers required for IM1 ─────────────────────────────────

def test_canonical_register_required_for_im1(tmp_path):
    repo = _full_repo(tmp_path, skip_files={"02_registers/DELIVERABLE_REGISTER.md"})
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    im1 = next(c for c in result["criterion_evidence"] if c["criterion_id"] == "IM1")
    assert any(m["key"] == "deliverable_register" for m in im1["missing_evidence"])
    assert im1["coverage_ratio"] < 1.0


def test_risk_register_honestly_reported_as_not_declared(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    im1 = next(c for c in result["criterion_evidence"] if c["criterion_id"] == "IM1")
    assert any(m["key"] == "risk_register" for m in im1["missing_evidence"])
    assert result["register_readiness"]["risk_register"]["path"] is None


# ── Freshness and result are independent axes ────────────────────────────

def test_freshness_and_result_independent(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    _write_state(state_dir, "guardian_state.json", {
        "repo_commit": "deadbeef", "guardian_status": "PASS", "findings": [],
    })
    result = ea.assemble(repo, state_dir)
    guardian_src = result["source_states"]["guardian"]
    assert guardian_src["freshness"] == "STALE"
    assert guardian_src["result"] == "OK"  # stale snapshot, but clean content -- never conflated


def test_stale_state_is_reported_as_contradiction(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    _write_state(state_dir, "guardian_state.json", {
        "repo_commit": "deadbeef", "guardian_status": "PASS", "findings": [],
    })
    result = ea.assemble(repo, state_dir)
    stale_contradictions = [c for c in result["contradictions"] if c["id"] == "contradiction-stale-state-guardian"]
    assert len(stale_contradictions) == 1


# ── Contradictions are deterministic (same inputs -> same outputs) ──────

def test_contradictions_are_deterministic(tmp_path):
    repo = _full_repo(tmp_path, empty_partners=True)
    state_dir = _state_dir(tmp_path)
    r1 = ea.assemble(repo, state_dir)
    r2 = ea.assemble(repo, state_dir)
    ids1 = sorted(c["id"] for c in r1["contradictions"])
    ids2 = sorted(c["id"] for c in r2["contradictions"])
    assert ids1 == ids2


# ── Proposal repo remains unchanged; no writes outside state_dir ────────

def test_proposal_repo_unmodified_after_assemble(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    ea.assemble(repo, state_dir)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after == ""


def test_state_only_written_to_state_dir(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    other_dir = tmp_path / "not_state"
    other_dir.mkdir()
    before = sorted(p.name for p in other_dir.iterdir())
    ea.assemble(repo, state_dir)
    after = sorted(p.name for p in other_dir.iterdir())
    assert before == after == []
    assert (state_dir / ea.STATE_FILENAME).exists()


# ── Schema validation ─────────────────────────────────────────────────

def test_evidence_state_validates_against_schema(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    schema = json.loads((CONTRACTS_DIR / "proposal_evidence_state.schema.json").read_text())
    jsonschema.validate(result, schema)


# ── Deterministic contradiction-type coverage (spot checks) ─────────────

def test_wp_negated_reference_is_not_a_false_positive(tmp_path):
    repo = _full_repo(tmp_path, extra_files={
        "04_proposal/IMPLEMENTATION.md": "Work plan WP1 WP2 WP3 WP4 WP5 WP6. No WP7 is part of the current scope.",
    })
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    wp_contradictions = [c for c in result["contradictions"] if c["id"] == "contradiction-wp-number-mismatch"]
    assert wp_contradictions == []


def test_wp_unexplained_reference_is_flagged(tmp_path):
    repo = _full_repo(tmp_path, extra_files={
        "04_proposal/IMPLEMENTATION.md": "Work plan WP1 WP2 WP3 WP4 WP5 WP6 WP9 all contribute.",
    })
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    wp_contradictions = [c for c in result["contradictions"] if c["id"] == "contradiction-wp-number-mismatch"]
    assert len(wp_contradictions) == 1
    assert "9" in wp_contradictions[0]["claim"]


def test_trl_claim_unsupported_by_baseline(tmp_path):
    repo = _full_repo(tmp_path, extra_files={
        "04_proposal/EXCELLENCE.md": "Objectives. TRL 7 already achieved.",
        "00_baseline/FORISEC_M1_TECHNICAL_BASELINE.md": "No TRL figure stated here.",
    })
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    trl_contradictions = [c for c in result["contradictions"] if c["id"] == "contradiction-trl-claim-unsupported"]
    assert len(trl_contradictions) == 1
    assert trl_contradictions[0]["criterion"] == "E1"


def test_trl_claim_supported_produces_no_contradiction(tmp_path):
    repo = _full_repo(tmp_path)  # EXCELLENCE.md says "TRL 5", baseline says "Starting TRL 5"
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    trl_contradictions = [c for c in result["contradictions"] if c["id"] == "contradiction-trl-claim-unsupported"]
    assert trl_contradictions == []


def test_deliverable_milestone_not_registered(tmp_path):
    repo = _full_repo(tmp_path, extra_files={
        "04_proposal/IMPLEMENTATION.md": "Work plan WP1 WP2 WP3 WP4 WP5 WP6. Delivers D9.9 and M99.",
    })
    state_dir = _state_dir(tmp_path)
    result = ea.assemble(repo, state_dir)
    dm = [c for c in result["contradictions"] if c["id"] == "contradiction-deliverable-milestone-not-registered"]
    assert len(dm) == 1


def test_guardian_broken_reference_becomes_contradiction(tmp_path):
    repo = _full_repo(tmp_path)
    state_dir = _state_dir(tmp_path)
    import subprocess as sp
    commit = sp.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
    _write_state(state_dir, "guardian_state.json", {
        "repo_commit": commit, "guardian_status": "FAIL",
        "findings": [{
            "id": "broken-self-ref-MISSING.md", "canonical_issue_key": "MISSING.md", "target": "MISSING.md",
            "occurrence_count": 2, "severity": "critical", "title": "Broken self-reference: MISSING.md",
            "description": "does not resolve", "source": "00_baseline/X.md",
            "affected_sources": [{"source_file": "00_baseline/X.md", "raw_reference": "MISSING.md"}],
        }],
    })
    result = ea.assemble(repo, state_dir)
    guardian_contradictions = [c for c in result["contradictions"] if c["id"].startswith("contradiction-guardian-")]
    assert len(guardian_contradictions) == 1
    assert guardian_contradictions[0]["severity"] == "critical"
