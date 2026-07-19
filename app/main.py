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
from app.state_reader import read_all_state, get_live_repo_commit

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


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {})
