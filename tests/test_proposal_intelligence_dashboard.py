"""
Phase 3 — dashboard API + UI wiring tests for Agent 5.
"""
import importlib
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_app(repo_root, state_dir):
    os.environ["FORISEC_REPO_ROOT"] = str(repo_root)
    os.environ["FORISEC_STATE_DIR"] = str(state_dir)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    return main_module.app


def test_history_api_returns_empty_list_when_no_snapshots(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/api/v1/proposal-intelligence/history")
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_missing_agent5_state_reports_unavailable(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    d = client.get("/api/v1/proposal-intelligence").json()
    assert d["available"] is False
    assert d["status"] == "AGENT_UNAVAILABLE"


def test_agent5_state_available_via_api(fake_repo, state_dir):
    (state_dir / "proposal_intelligence_state.json").write_text(json.dumps({
        "schema_version": "1.0", "scoring_model_version": "1.0",
        "agent_id": "agent5_proposal_intelligence", "repo_commit": "x",
        "run_timestamp": "x", "mode": "DIAGNOSTIC", "overall_status": "DIAGNOSTIC_COMPLETE",
        "diagnostic_score": {"excellence": 3, "impact": 3, "implementation": 3, "total": 9, "max_total": 15},
        "canonical_score": None, "promotion_status": "PENDING_REVIEW",
        "section_scores": [], "competitive_assessment": {"score": 2.0, "label": "WEAK", "components": {}},
        "weaknesses": [], "evidence_packs": [], "fix_packs": [],
        "timeline_summary": {"baseline": None, "latest": None, "total_gain": 0.0, "snapshot_count": 0},
        "findings": [],
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    d = client.get("/api/v1/proposal-intelligence").json()
    assert d["available"] is True
    assert d["mode"] == "DIAGNOSTIC"
    assert d["canonical_score"] is None


def test_history_api_returns_snapshot_records(fake_repo, state_dir):
    hist_dir = state_dir / "history"
    hist_dir.mkdir()
    (hist_dir / "evaluation_2026-01-01T00-00-00_abc123.json").write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00", "repo_commit": "abc123", "scoring_model_version": "1.0",
        "total": 9.0, "excellence": 3.0, "impact": 3.0, "implementation": 3.0,
        "competitive_score": 2.0, "critical_finding_count": 0, "promotion_status": "PENDING_REVIEW",
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/api/v1/proposal-intelligence/history")
    records = resp.json()["records"]
    assert len(records) == 1
    assert records[0]["repo_commit"] == "abc123"


def test_dashboard_does_not_write_state_for_agent5(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    client.get("/api/v1/proposal-intelligence")
    client.get("/api/v1/proposal-intelligence/history")
    assert not (state_dir / "proposal_intelligence_state.json").exists()
    assert not (state_dir / "history").exists()


def test_dashboard_does_not_import_agents_still_holds():
    # Re-affirms the Phase 2A isolation guarantee after Agent 5 wiring.
    import ast
    app_files = list((PROJECT_ROOT / "app").rglob("*.py"))
    offenders = []
    for f in app_files:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "agents":
                        offenders.append(str(f))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "agents":
                    offenders.append(str(f))
    assert offenders == []


# ── UI wiring (HTML/JS presence checks -- no headless browser here) ───────

def test_dashboard_html_contains_four_agent5_panel_titles():
    html = (PROJECT_ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "Agent 5 — Detailed Evaluation" in html
    assert "Competitive Score" in html
    assert "Improvement Loop" in html
    assert "Evaluation Timeline" in html


def test_dashboard_js_renders_all_four_agent5_panels():
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    for fn in ["function renderEval5(", "function renderCompetitive(",
               "function renderImprovementLoop(", "function renderTimeline("]:
        assert fn in js


def test_dashboard_js_includes_agent5_in_status_cards():
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "Agent 5" in js and "proposalIntelligence" in js


def test_dashboard_js_status_cards_show_freshness_and_result_separately():
    # STEP 2 OF 2: Agent Status Cards must never collapse to one green pill
    # just because a state file is FRESH -- freshness and result render as
    # two independent badges.
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "function agentResult(" in js
    assert "FRESHNESS_PILL" in js and "RESULT_PILL" in js


def test_dashboard_js_never_labels_canonical_score_as_official():
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "OFFICIAL" not in js.upper().replace("NOT OFFICIAL", "")


def test_dashboard_js_shows_not_approved_for_null_canonical_score():
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "NOT APPROVED" in js
