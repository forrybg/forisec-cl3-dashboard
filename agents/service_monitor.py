"""
agents/service_monitor.py

Read-only, HTTP-only health/status poller for the sibling foritech-os
GPU/semantic-search services (foritech-gpu-embeddings, foritech-gpu-
reranker, foritech-search), plus a live evidence-strength check for
each open Agent 5 weakness against the search index.

SCOPE EXCEPTION (documented, deliberate, same pattern as
agents/budget_reader.py's WP-repo exception): every other agent in
this package only ever reads FORISEC_REPO_ROOT and FORISEC_STATE_DIR.
This agent additionally makes plain, unauthenticated HTTP GET/POST
requests to a fixed, small set of well-known LOOPBACK ports that
happen to be served by processes belonging to the older foritech-os
installation on this same host. This is NOT a runtime dependency in
the sense forbidden by tests/test_isolation.py:

  - No foritech-os Python module is ever imported (verified by
    test_isolation.py's AST import scan -- this file imports only
    stdlib json/urllib plus agents.common).
  - No foritech-os file path is ever read or written.
  - Every call is wrapped in try/except and degrades to a single,
    honest "UNAVAILABLE" status per service -- a foritech-os outage
    can never crash this agent or block the rest of the pipeline.
  - This agent's own output (services_status.json) is written only
    to FORISEC_STATE_DIR, exactly like every other agent.

If foritech-os is ever decommissioned or moved, this agent simply
reports every service UNAVAILABLE forever -- it never errors out and
never blocks scoring, promotion, or any other agent.

Usage: python -m agents.service_monitor
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from agents.common import atomic_write_json, base_state, read_json_or_none

STATE_FILENAME = "services_status.json"
PROPOSAL_INTELLIGENCE_FILENAME = "proposal_intelligence_state.json"

DEFAULT_HOST = "127.0.0.1"

# Fixed, known list of sibling services -- not derived from process
# scanning, so an unrelated local service can never be picked up.
SERVICES = [
    ("Embeddings", 8101),
    ("Reranker", 8102),
    ("Search API", 8103),
]

SEARCH_PORT = 8103
HTTP_TIMEOUT_SECONDS = 5
SEARCH_TOP_K = 5


def _host() -> str:
    return os.environ.get("FORISEC_EXTERNAL_SERVICES_HOST", DEFAULT_HOST)


def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _http_post_json(url: str, payload: dict, timeout: int = HTTP_TIMEOUT_SECONDS) -> dict | None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def check_services() -> dict:
    """
    Read-only HTTP health check against each known service's /health
    endpoint. Never touches systemd, never imports foritech-os code.
    Returns one entry per service, always -- UP/DOWN never raises.
    """
    host = _host()
    services = []
    cuda = None
    for name, port in SERVICES:
        health = _http_get_json(f"http://{host}:{port}/health")
        up = bool(health) and health.get("status") in ("ok", "degraded")
        entry = {
            "name": name,
            "port": port,
            "status": "UP" if up else "DOWN",
        }
        if health:
            if health.get("status") == "degraded":
                entry["status"] = "DEGRADED"
            if "device" in health:
                entry["device"] = health["device"]
                if health["device"] == "cuda":
                    cuda = True
                elif cuda is None:
                    cuda = False
            if "indexed" in health:
                entry["indexed_chunks"] = health.get("indexed")
        services.append(entry)
    return {"services": services, "cuda": cuda}


def check_index() -> dict:
    """Read-only /health + /stats calls against the Search API. Degrades
    to an honest 'unavailable' shape, never fabricates counts.

    The real Search API's /stats only returns a by_category breakdown
    (no top-level indexed_files/chunks/last_indexed keys); /health
    carries the authoritative total_chunks/indexed count. Both are
    combined here defensively -- any field this service doesn't
    provide is left as None rather than guessed."""
    host = _host()
    health = _http_get_json(f"http://{host}:{SEARCH_PORT}/health")
    stats = _http_get_json(f"http://{host}:{SEARCH_PORT}/stats")
    if not health and not stats:
        return {"available": False, "indexed_files": None, "chunks": None,
                "last_indexed": None, "by_category": {}}
    by_category = (stats or {}).get("by_category", {})
    chunks = (health or {}).get("total_chunks", (health or {}).get("indexed"))
    if chunks is None and by_category:
        chunks = sum(by_category.values())
    return {
        "available": True,
        "indexed_files": (stats or {}).get("indexed_files"),
        "chunks": chunks,
        "last_indexed": (stats or {}).get("last_indexed"),
        "by_category": by_category,
    }


def _classify_evidence(best_score: float | None, evidence_count: int) -> str:
    if not evidence_count or best_score is None:
        return "NEEDS_SOURCE_DOC"
    if best_score >= 0.5:
        return "PENDING_REVIEW"
    if best_score >= 0.2:
        return "WEAK_EVIDENCE"
    return "NEEDS_SOURCE_DOC"


def search_evidence_for_weaknesses(weaknesses: list[dict], search_up: bool) -> list[dict]:
    """
    For each open Agent-5 weakness, run one live semantic-search query
    against the Search API (title text only -- never proposal file
    contents beyond what proposal_intelligence_state.json already
    recorded) and classify the strength of what comes back. If the
    Search API is down, every item degrades to NEEDS_SOURCE_DOC with
    evidence_count=0 rather than raising or guessing.
    """
    host = _host()
    items = []
    for w in weaknesses:
        query = re.sub(r"\s+", " ", (w.get("title") or w.get("description") or "")).strip()
        results = []
        if search_up and query:
            resp = _http_post_json(
                f"http://{host}:{SEARCH_PORT}/search",
                {"query": query, "top_k": SEARCH_TOP_K, "rerank": True},
            )
            if resp:
                results = resp.get("results", [])
        best_score = max((r.get("score", 0.0) for r in results), default=None) if results else None
        items.append({
            "weakness_id": w.get("id"),
            "criterion": w.get("criterion"),
            "title": w.get("title"),
            "query": query,
            "evidence_count": len(results),
            "best_score": round(best_score, 3) if best_score is not None else None,
            "status": _classify_evidence(best_score, len(results)),
        })
    return items


def _chain(has_weaknesses: bool, has_live_evidence: bool, has_fix_packs: bool, all_pending: bool) -> list[dict]:
    def step(label, state):
        return {"label": label, "state": state}

    return [
        step("Agent 5 weakness", "ok" if has_weaknesses else "idle"),
        step("Search evidence", "ok" if has_live_evidence else ("warn" if has_weaknesses else "idle")),
        step("Evidence Pack", "ok" if has_weaknesses else "idle"),
        step("Fix Pack", "ok" if has_fix_packs else "idle"),
        step("PENDING_REVIEW", "warn" if all_pending else ("ok" if has_fix_packs else "idle")),
        step("Human approval", "warn" if all_pending else "idle"),
        step("Apply / Commit + re-eval", "idle"),
    ]


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("service_monitor", repo_root)

    svc = check_services()
    index = check_index()
    search_up = any(s["port"] == SEARCH_PORT and s["status"] in ("UP", "DEGRADED") for s in svc["services"])

    pi_state = read_json_or_none(state_dir / PROPOSAL_INTELLIGENCE_FILENAME) or {}
    weaknesses = pi_state.get("weaknesses", [])
    fix_packs = pi_state.get("fix_packs", [])

    evidence_items = search_evidence_for_weaknesses(weaknesses, search_up)
    has_live_evidence = any(i["evidence_count"] > 0 for i in evidence_items)
    all_pending = bool(fix_packs) and all(f.get("status") == "PENDING_REVIEW" for f in fix_packs)

    result = {
        **base,
        "status": "completed",
        "available": True,
        "source_note": (
            "Read-only HTTP GET/POST to sibling foritech-* services on "
            "loopback ports 8101-8103 (health/stats/search only). No "
            "foritech-os code is imported. Any unreachable service "
            "degrades to DOWN/UNAVAILABLE here -- never raises, never "
            "blocks the rest of the pipeline."
        ),
        "services": svc["services"],
        "cuda": svc["cuda"],
        "index": index,
        "evidence_items": evidence_items,
        "fix_packs_summary": {
            "count": len(fix_packs),
            "all_pending_review": all_pending,
        },
        "chain": _chain(bool(weaknesses), has_live_evidence, bool(fix_packs), all_pending),
        "proposal_intelligence_snapshot": {
            "available": bool(pi_state),
            "diagnostic_score": (pi_state.get("diagnostic_score") or {}).get("total"),
            "overall_status": pi_state.get("overall_status"),
        },
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "service_monitor")


if __name__ == "__main__":
    main()
