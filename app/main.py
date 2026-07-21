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
from context import retrieval as context_retrieval
from context.retrieval import RetrievalError, SECTION_ALLOWLIST

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
    """Legacy compatibility endpoint. Never exposes an absolute
    filesystem path (repo_root/state_dir were removed -- use
    /health/live and /health/ready for real health/readiness)."""
    return {"status": "ok"}


@app.get("/health/live")
def health_live():
    """True as soon as the application process is up and able to
    handle a request. Says nothing about whether the context bundle or
    index is valid -- see /health/ready for that."""
    return {"status": "ok", "live": True}


@app.get("/health/ready")
def health_ready():
    """Readiness = bootstrap available & schema-valid, context.db
    available & schema-valid, both bound to the SAME proposal repo
    commit (no mixed generation), and neither is STALE/UNAVAILABLE.
    Read-only: never builds the bundle or the index, never writes."""
    reasons = []

    bootstrap = read_context_bootstrap(STATE_DIR, REPO_ROOT)
    bootstrap_ok = bool(bootstrap.get("available")) and bootstrap.get("freshness") == "FRESH"
    if not bootstrap.get("available"):
        reasons.append(f"bootstrap unavailable: {bootstrap.get('reason')}")
    elif bootstrap.get("freshness") != "FRESH":
        reasons.append(f"bootstrap freshness={bootstrap.get('freshness')}")

    db_meta = context_retrieval._read_db_meta(STATE_DIR)
    db_ok = db_meta is not None
    if not db_ok:
        reasons.append("context.db unavailable or invalid.")

    mixed_generation = False
    if bootstrap.get("available") and db_meta is not None:
        if bootstrap.get("repo_commit") != db_meta.get("proposal_repo_commit"):
            mixed_generation = True
            reasons.append(
                f"mixed generation: bootstrap repo_commit={bootstrap.get('repo_commit')} "
                f"!= context.db repo_commit={db_meta.get('proposal_repo_commit')}"
            )

    live_commit = get_live_repo_commit(REPO_ROOT)
    commit_match = bool(live_commit) and db_meta is not None and live_commit == db_meta.get("proposal_repo_commit")
    if db_meta is not None and not commit_match:
        reasons.append(f"context.db repo_commit does not match live proposal repo HEAD.")

    ready = bootstrap_ok and db_ok and not mixed_generation and commit_match
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "bootstrap_available": bool(bootstrap.get("available")),
        "bootstrap_freshness": bootstrap.get("freshness"),
        "context_db_available": db_ok,
        "mixed_generation": mixed_generation,
        "reasons": reasons,
    }


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


@app.get("/api/v1/context/section/{section}")
def api_context_section(section: str):
    """LEVEL 2 -- read-only structured summary + snippets + exact
    sources for one allowlisted section. Never builds the bundle/index,
    never writes, never accepts a filesystem path as `section` (only a
    bare name from SECTION_ALLOWLIST is ever dispatched)."""
    bootstrap = read_context_bootstrap(STATE_DIR, REPO_ROOT)
    try:
        return context_retrieval.get_section(STATE_DIR, REPO_ROOT, section, bootstrap)
    except RetrievalError as e:
        return {"available": False, "error": e.code, "reason": e.message}


@app.get("/api/v1/context/search")
def api_context_search(q: str, top_k: int = 5, section: str | None = None):
    """LEVEL 3 -- read-only FTS5 lexical search (+ semantic re-score
    only if the embeddings worker and stored embeddings are both
    actually available -- semantic_used is reported honestly either
    way). Never builds the index, never writes, never returns a full
    document -- only bounded snippets."""
    try:
        return context_retrieval.search(STATE_DIR, REPO_ROOT, q, top_k=top_k, section=section)
    except RetrievalError as e:
        return {"available": False, "error": e.code, "reason": e.message}


@app.get("/api/v1/context/source")
def api_context_source(path: str):
    """LEVEL 3 -- read-only bounded view of one canonical source's
    chunks. `path` must already be a source in the current context.db
    generation (itself built only from config/canonical_documents.json)
    -- absolute paths, `../` traversal, and any path outside that
    allowlist are rejected before any query is even run."""
    try:
        return context_retrieval.get_source(STATE_DIR, REPO_ROOT, path)
    except RetrievalError as e:
        return {"available": False, "error": e.code, "reason": e.message}


@app.get("/api/v1/context/repo-map")
def api_context_repo_map():
    """Deterministic catalog of THIS SERVICE's own codebase (app/,
    agents/, pipeline/, context/, plus refresh_agents.sh and the
    context bundle schema) -- path, kind, summary (from each module's
    own docstring/first line), top-level functions/classes, line count.
    Never the proposal repo (that is `sources`/`chunks`). Built fresh
    every context.db generation by context/index_builder.py's
    _scan_repo_map(); read-only here, never rebuilt on request."""
    return context_retrieval.get_repo_map(STATE_DIR, REPO_ROOT)


# Bumped whenever a static asset (dashboard.css / dashboard.js) changes,
# so a browser tab left open across a deploy is forced to refetch instead
# of silently rendering with stale cached CSS/JS.
ASSET_VERSION = "2026-07-21-5"


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"asset_version": ASSET_VERSION})
