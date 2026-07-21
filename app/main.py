"""
app/main.py

FastAPI application for the standalone FORISEC CL3 dashboard.

Hard isolation rules enforced here (see tests/test_isolation.py):
- no import of anything under the old system;
- no legacy in-process memory database, no legacy proposal-scoring
  agent, no legacy competitive score, no legacy intelligence loop;
- reads only JSON from FORISEC_STATE_DIR;
- never scans markdown at request time, never runs agents, never
  writes state.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config_or_exit
from app.state_reader import read_all_state, get_live_repo_commit, read_history, read_context_bootstrap

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="FORISEC CL3 Dashboard", version="0.1.0")

_config = load_config_or_exit()
REPO_ROOT: Path = _config["repo_root"]
STATE_DIR: Path = _config["state_dir"]

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _compute_summary() -> dict:
    state = read_all_state(STATE_DIR, REPO_ROOT)
    docs, evaluation, guardian, supervisor = (
        state["docs"], state["evaluation"], state["guardian"], state["supervisor"]
    )
    decisions = state["decisions"]
    budget = state["budget"]

    fresh_count = sum(1 for s in (docs, evaluation, guardian) if s.get("freshness") == "FRESH")

    guardian_critical = [f for f in guardian.get("findings", []) if f.get("severity") == "critical"]
    eval_critical = [f for f in evaluation.get("findings", []) if f.get("severity") == "critical"]
    critical_findings = guardian_critical + eval_critical

    pending_reviews = [
        d for d in docs.get("documents", [])
        if d.get("status") in ("REVIEW_REQUIRED", "EVIDENCE_REQUIRED")
    ]

    if supervisor.get("available"):
        overall_status = supervisor.get("overall_status", "UNKNOWN")
    elif critical_findings:
        overall_status = "CRITICAL"
    elif not all(s.get("available") for s in (docs, evaluation, guardian)):
        overall_status = "DEGRADED"
    elif pending_reviews:
        overall_status = "REVIEW"
    else:
        overall_status = "OK"

    return {
        "overall_status": overall_status,
        "live_repo_commit": get_live_repo_commit(REPO_ROOT),
        "fresh_state_files": fresh_count,
        "fresh_state_files_total": 3,
        "critical_finding_count": len(critical_findings),
        "pending_review_count": len(pending_reviews),
        "docs": docs,
        "evaluation": evaluation,
        "guardian": guardian,
        "supervisor": supervisor,
        "decisions": decisions,
        "budget": budget,
    }


@app.get("/health")
def health():
    return {"status": "ok", "repo_root": str(REPO_ROOT), "state_dir": str(STATE_DIR)}


@app.get("/api/v1/summary")
def api_summary():
    return _compute_summary()


@app.get("/api/v1/docs")
def api_docs():
    return read_all_state(STATE_DIR, REPO_ROOT)["docs"]


@app.get("/api/v1/evaluation")
def api_evaluation():
    return read_all_state(STATE_DIR, REPO_ROOT)["evaluation"]


@app.get("/api/v1/guardian")
def api_guardian():
    return read_all_state(STATE_DIR, REPO_ROOT)["guardian"]


@app.get("/api/v1/supervisor")
def api_supervisor():
    return read_all_state(STATE_DIR, REPO_ROOT)["supervisor"]


@app.get("/api/v1/proposal-intelligence")
def api_proposal_intelligence():
    return read_all_state(STATE_DIR, REPO_ROOT)["proposal_intelligence"]


@app.get("/api/v1/decisions")
def api_decisions():
    return read_all_state(STATE_DIR, REPO_ROOT)["decisions"]


@app.get("/api/v1/budget")
def api_budget():
    return read_all_state(STATE_DIR, REPO_ROOT)["budget"]


@app.get("/api/v1/proposal-intelligence/history")
def api_proposal_intelligence_history():
    return {"available": True, "records": read_history(STATE_DIR)}


@app.get("/api/v1/evidence")
def api_evidence():
    """Read-only: the evidence bundle produced by pipeline.evidence_assembler.
    Never runs the pipeline, never scores the proposal -- this endpoint only
    surfaces what pipeline/evidence_assembler.py already wrote to disk."""
    return read_all_state(STATE_DIR, REPO_ROOT)["evidence"]


@app.get("/api/v1/evidence/coverage")
def api_evidence_coverage():
    evidence = read_all_state(STATE_DIR, REPO_ROOT)["evidence"]
    if not evidence.get("available"):
        return evidence
    return {
        "available": True,
        "freshness": evidence.get("freshness"),
        "result": evidence.get("result"),
        "coverage_summary": evidence.get("coverage_summary"),
        "criterion_evidence": [
            {k: ce[k] for k in ("criterion_id", "coverage_ratio", "evidence_quality", "freshness", "result")}
            for ce in evidence.get("criterion_evidence", [])
        ],
        "budget_readiness": evidence.get("budget_readiness"),
        "partner_readiness": evidence.get("partner_readiness"),
        "register_readiness": evidence.get("register_readiness"),
        "technical_readiness": evidence.get("technical_readiness"),
        "guardian_summary": evidence.get("guardian_summary"),
    }


@app.get("/api/v1/evidence/contradictions")
def api_evidence_contradictions():
    evidence = read_all_state(STATE_DIR, REPO_ROOT)["evidence"]
    if not evidence.get("available"):
        return evidence
    return {"available": True, "contradictions": evidence.get("contradictions", []),
            "cross_document_checks": evidence.get("cross_document_checks", [])}


@app.get("/api/v1/services")
def api_services():
    """Read-only: whatever agents/service_monitor.py last wrote to
    services_status.json. This endpoint never calls foritech-* itself --
    only agents/service_monitor.py (run via scripts/refresh_agents.sh)
    does that, on the timer's existing cadence."""
    return read_all_state(STATE_DIR, REPO_ROOT)["services"]


@app.get("/api/v1/context/bootstrap")
def api_context_bootstrap():
    """Read-only: whatever pipeline/context_builder.py last wrote to
    project_context_state.json. PHASE 1 only -- this endpoint never
    builds the bundle, never runs agents, never scans the proposal repo,
    and never writes anything. It validates the stored bundle against
    contracts/project_context_state.schema.json and recomputes freshness
    against the live proposal repo commit before returning it. No
    absolute filesystem path is ever included in the response."""
    return read_context_bootstrap(STATE_DIR, REPO_ROOT)


# Bumped whenever a static asset (dashboard.css / dashboard.js) changes,
# so a browser tab left open across a deploy is forced to refetch instead
# of silently rendering with stale cached CSS/JS.
ASSET_VERSION = "2026-07-21-4"


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"asset_version": ASSET_VERSION})
