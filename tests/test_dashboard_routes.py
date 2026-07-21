import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_app(repo_root: Path, state_dir: Path):
    """Fresh app instance per test, with env vars pointed at fixtures --
    never at the real proposal repo or real state dir."""
    os.environ["FORISEC_REPO_ROOT"] = str(repo_root)
    os.environ["FORISEC_STATE_DIR"] = str(state_dir)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    return main_module.app


def test_root_returns_200(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_health_returns_200(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("route", [
    "/api/v1/summary", "/api/v1/docs", "/api/v1/evaluation",
    "/api/v1/guardian", "/api/v1/supervisor",
    "/api/v1/evidence", "/api/v1/evidence/coverage", "/api/v1/evidence/contradictions",
    "/api/v1/services",
])
def test_api_v1_routes_return_200(fake_repo, state_dir, route):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get(route)
    assert resp.status_code == 200


def test_evidence_api_reflects_written_bundle(fake_repo, state_dir):
    (state_dir / "proposal_evidence_state.json").write_text(json.dumps({
        "schema_version": "1.0", "evidence_model_version": "1.0", "repo_commit": "x",
        "run_timestamp": "x", "freshness": "FRESH", "result": "WARN",
        "source_states": {}, "criterion_evidence": [], "cross_document_checks": [],
        "contradictions": [{"id": "c1", "criterion": "IM1", "severity": "high", "claim_source": "a",
                            "claim": "x", "contradicting_source": "b", "reason": "r",
                            "affected_files": [], "repo_commit": "x"}],
        "missing_evidence": [], "guardian_summary": {}, "partner_readiness": [],
        "budget_readiness": {}, "resource_readiness": {}, "register_readiness": {},
        "technical_readiness": {}, "coverage_summary": {"contraction_count": 1}, "findings": [],
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    evidence = client.get("/api/v1/evidence").json()
    assert evidence["result"] == "WARN"
    contradictions = client.get("/api/v1/evidence/contradictions").json()
    assert len(contradictions["contradictions"]) == 1


def test_evidence_panel_and_evidence_gated_banner_present_in_html(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    html = client.get("/").text
    assert "Proposal Evidence Coverage" in html
    # STEP 2 OF 2: the headline banner is now the evidence-gated score,
    # not the old keyword/word-count "STRUCTURAL HEURISTIC SCORE".
    assert "EVIDENCE-GATED DIAGNOSTIC SCORE" in html
    assert "STRUCTURAL HEURISTIC SCORE" not in html
    assert "Text completeness" in html
    assert "Not an evaluator score" in html


def test_missing_state_reports_unavailable(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)  # state_dir is empty
    client = TestClient(app)
    d = client.get("/api/v1/docs").json()
    assert d["available"] is False
    assert d["status"] == "AGENT_UNAVAILABLE"
    summary = client.get("/api/v1/summary").json()
    assert summary["overall_status"] == "DEGRADED"


def test_stale_commit_reflected_in_summary(fake_repo, state_dir):
    (state_dir / "guardian_state.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "repository_guardian",
        "repo_commit": "deadbeef", "run_timestamp": "x",
        "status": "completed", "guardian_status": "PASS", "findings": [],
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    d = client.get("/api/v1/guardian").json()
    assert d["freshness"] == "STALE"


def test_score_null_shows_not_implemented_not_success(fake_repo, state_dir):
    (state_dir / "docs_state.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "docs_controller",
        "repo_commit": "x", "run_timestamp": "x", "status": "completed",
        "documents": [
            {"path": p, "title": p, "status": "DRAFT"}
            for p in ["04_proposal/EXCELLENCE.md", "04_proposal/IMPACT.md", "04_proposal/IMPLEMENTATION.md"]
        ],
    }))
    (state_dir / "evaluation_state.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "proposal_evaluator",
        "repo_commit": "x", "run_timestamp": "x", "status": "completed",
        "overall_status": "ACTIVE_DIAGNOSTIC_MODE", "mode": "diagnostic", "score": None,
        "findings": [{"id": "scoring-not-yet-implemented", "severity": "info", "title": "x"}],
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    html = client.get("/").text
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text()
    assert "SCORING NOT IMPLEMENTED" in js
    assert "SCORE UNAVAILABLE" in js
    assert "d.score === null" in js


def test_critical_guardian_finding_visible_in_summary(fake_repo, state_dir):
    (state_dir / "guardian_state.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "repository_guardian",
        "repo_commit": "x", "run_timestamp": "x", "status": "completed",
        "guardian_status": "FAIL",
        "findings": [{"id": "x", "severity": "critical", "title": "broken ref", "source": "a.md"}],
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    summary = client.get("/api/v1/summary").json()
    assert summary["critical_finding_count"] == 1
    assert summary["overall_status"] in ("CRITICAL",)


def test_services_api_reflects_written_state(fake_repo, state_dir):
    (state_dir / "services_status.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "service_monitor", "repo_commit": "x",
        "run_timestamp": "2026-07-21T00:00:00", "status": "completed",
        "services": [
            {"name": "Embeddings", "port": 8101, "status": "UP"},
            {"name": "Reranker", "port": 8102, "status": "UP", "device": "cuda"},
            {"name": "Search API", "port": 8103, "status": "UP"},
        ],
        "cuda": True,
        "index": {"available": True, "indexed_files": None, "chunks": 2504,
                   "last_indexed": None, "by_category": {"budget": 144}},
        "evidence_items": [{"weakness_id": "weakness-E1", "criterion": "E1", "title": "t",
                             "query": "t", "evidence_count": 1, "best_score": 0.6,
                             "status": "PENDING_REVIEW"}],
        "fix_packs_summary": {"count": 1, "all_pending_review": True},
        "chain": [{"label": "Agent 5 weakness", "state": "ok"}],
        "proposal_intelligence_snapshot": {"available": True, "diagnostic_score": 6.5,
                                            "overall_status": "BLOCKED"},
    }))
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get("/api/v1/services")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["cuda"] is True
    assert data["services"][2]["port"] == 8103
    assert data["evidence_items"][0]["status"] == "PENDING_REVIEW"


def test_services_api_unavailable_when_no_state(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    data = client.get("/api/v1/services").json()
    assert data["available"] is False
    assert data["status"] == "AGENT_UNAVAILABLE"


def test_dashboard_html_includes_services_panel_markup(fake_repo, state_dir):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    html = client.get("/").text
    assert 'id="services-table"' in html
    assert 'id="services-index"' in html
    assert 'id="live-evidence-table"' in html
    assert 'id="pipeline-row"' in html


def test_dashboard_js_renders_service_chain_and_panel(fake_repo, state_dir):
    js = (PROJECT_ROOT / "app" / "static" / "dashboard.js").read_text()
    assert "renderServiceChain" in js
    assert "renderServicesPanel" in js
    assert "/api/v1/services" in js
    assert "renderImprovementLoop(eval5, services)" in js
