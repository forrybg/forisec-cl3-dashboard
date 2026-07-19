"""
Phase 2A.2 (final UI pass) tests: all four agents must have an
equally clear, dedicated panel on the standalone dashboard.

Some assertions are HTML-template checks (the four headings and the
summary-field element ids exist server-side), others are JS-source
checks (the client-side rendering logic exists) -- there is no
headless browser in this environment, so full DOM-rendering assertions
are not possible; this mirrors the approach already used for the
score-null guard in test_dashboard_routes.py.
"""
import json

from fastapi.testclient import TestClient

PROJECT_ROOT_MARKERS = ("app", "agents", "contracts", "tests")


def _dashboard_html_and_js(project_root):
    html = (project_root / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    js = (project_root / "app" / "static" / "dashboard.js").read_text(encoding="utf-8")
    return html, js


def test_dashboard_html_contains_all_four_agent_headings(tmp_path):
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    html, _ = _dashboard_html_and_js(project_root)
    assert "Agent 1 — Documentation Controller" in html
    assert "Agent 2 — Proposal Evaluator" in html
    assert "Agent 3 — Repository Guardian" in html
    assert "Agent 4 — Project Supervisor" in html


def test_dashboard_html_panels_appear_in_order(tmp_path):
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    html, _ = _dashboard_html_and_js(project_root)
    i1 = html.index("Agent 1 — Documentation Controller")
    i2 = html.index("Agent 2 — Proposal Evaluator")
    i3 = html.index("Agent 3 — Repository Guardian")
    i4 = html.index("Agent 4 — Project Supervisor")
    assert i1 < i2 < i3 < i4


def test_agent1_summary_fields_present_in_html(tmp_path):
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    html, _ = _dashboard_html_and_js(project_root)
    for field_id in ["docs-sum-overall", "docs-sum-freshness", "docs-sum-commit",
                      "docs-sum-planned", "docs-sum-frozen", "docs-sum-draft",
                      "docs-sum-review", "docs-sum-missing"]:
        assert f'id="{field_id}"' in html


def test_agent3_summary_fields_present_in_html(tmp_path):
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    html, _ = _dashboard_html_and_js(project_root)
    for field_id in ["guardian-sum-status", "guardian-sum-freshness", "guardian-sum-critical",
                      "guardian-sum-high", "guardian-sum-medium", "guardian-sum-scanned"]:
        assert f'id="{field_id}"' in html


def test_agent4_supervisor_fields_present_in_html(tmp_path):
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    html, _ = _dashboard_html_and_js(project_root)
    for field_id in ["sup-sum-overall", "sup-sum-freshness", "sup-sum-commit",
                      "sup-sum-statefiles", "sup-sum-agent1", "sup-sum-agent2", "sup-sum-agent3"]:
        assert f'id="{field_id}"' in html


def test_js_renders_agent1_summary():
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    _, js = _dashboard_html_and_js(project_root)
    assert "function renderDocsSummary" in js
    assert "docs-sum-planned" in js


def test_js_renders_agent4_supervisor_details():
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    _, js = _dashboard_html_and_js(project_root)
    assert "function renderSupervisorSummary" in js
    assert "function renderSupervisor(" in js
    assert "deriveZones" in js


def test_js_maps_critical_status_to_critical_panel_class():
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    _, js = _dashboard_html_and_js(project_root)
    assert "CRITICAL: 'panel-critical'" in js or 'CRITICAL: "panel-critical"' in js


def test_css_defines_all_four_panel_status_classes():
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    css = (project_root / "app" / "static" / "dashboard.css").read_text(encoding="utf-8")
    for cls in [".panel-ok", ".panel-review", ".panel-degraded", ".panel-critical"]:
        assert cls in css


def test_missing_supervisor_state_returns_agent_unavailable(fake_repo, state_dir, monkeypatch):
    import importlib
    import os
    import sys
    os.environ["FORISEC_REPO_ROOT"] = str(fake_repo)
    os.environ["FORISEC_STATE_DIR"] = str(state_dir)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    client = TestClient(main_module.app)

    d = client.get("/api/v1/supervisor").json()
    assert d["available"] is False
    assert d["status"] == "AGENT_UNAVAILABLE"


def test_critical_supervisor_status_present_in_summary(fake_repo, state_dir):
    import importlib
    import os
    import sys
    (state_dir / "supervisor_state.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "project_supervisor",
        "repo_commit": "x", "run_timestamp": "x", "status": "completed",
        "overall_status": "CRITICAL",
        "state_files": {
            "docs_state.json": {"status": "OK", "repo_commit": "x"},
            "evaluation_state.json": {"status": "OK", "repo_commit": "x"},
            "guardian_state.json": {"status": "OK", "repo_commit": "x"},
        },
        "critical_finding_count": 1,
        "pending_review_count": 0,
        "evaluator_active_unscored": False,
        "findings": [{"severity": "critical", "title": "1 critical guardian finding(s)"}],
    }))
    os.environ["FORISEC_REPO_ROOT"] = str(fake_repo)
    os.environ["FORISEC_STATE_DIR"] = str(state_dir)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as main_module
    importlib.reload(main_module)
    client = TestClient(main_module.app)

    d = client.get("/api/v1/supervisor").json()
    assert d["available"] is True
    assert d["overall_status"] == "CRITICAL"


def test_score_null_still_scoring_not_implemented():
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    _, js = _dashboard_html_and_js(project_root)
    assert "SCORING NOT IMPLEMENTED" in js
    assert "d.score === null" in js
