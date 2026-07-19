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
])
def test_api_v1_routes_return_200(fake_repo, state_dir, route):
    app = _make_app(fake_repo, state_dir)
    client = TestClient(app)
    resp = client.get(route)
    assert resp.status_code == 200


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
