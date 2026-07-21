"""
Tests for agents/service_monitor.py -- the read-only HTTP status poller
for the sibling foritech-* GPU/search services. Every test mocks the
HTTP layer (_http_get_json / _http_post_json) directly; no test in
this file makes a real network call.
"""
import json
from pathlib import Path

import pytest

from agents import service_monitor as sm


# ── check_services ──────────────────────────────────────────────────────

def test_check_services_all_up(monkeypatch):
    def fake_get(url, timeout=5):
        if ":8101" in url:
            return {"status": "ok", "service": "foritech-gpu-embeddings"}
        if ":8102" in url:
            return {"status": "ok", "device": "cuda"}
        if ":8103" in url:
            return {"status": "ok", "total_chunks": 2504, "indexed": 2504}
        return None

    monkeypatch.setattr(sm, "_http_get_json", fake_get)
    result = sm.check_services()
    assert len(result["services"]) == 3
    assert all(s["status"] == "UP" for s in result["services"])
    assert result["cuda"] is True


def test_check_services_all_down(monkeypatch):
    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: None)
    result = sm.check_services()
    assert len(result["services"]) == 3
    assert all(s["status"] == "DOWN" for s in result["services"])
    assert result["cuda"] is None


def test_check_services_never_raises_on_partial_outage(monkeypatch):
    def fake_get(url, timeout=5):
        if ":8101" in url:
            raise AssertionError("should never propagate")
        return None

    def safe_get(url, timeout=5):
        try:
            return fake_get(url, timeout)
        except AssertionError:
            raise  # let the real function's own try/except handle it

    # service_monitor's own _http_get_json already swallows exceptions;
    # here we simulate one service returning a 'degraded' status.
    def fake_get2(url, timeout=5):
        if ":8103" in url:
            return {"status": "degraded", "total_chunks": 10}
        return None

    monkeypatch.setattr(sm, "_http_get_json", fake_get2)
    result = sm.check_services()
    statuses = {s["port"]: s["status"] for s in result["services"]}
    assert statuses[8101] == "DOWN"
    assert statuses[8102] == "DOWN"
    assert statuses[8103] == "DEGRADED"


# ── check_index ─────────────────────────────────────────────────────────

def test_check_index_unavailable_when_service_down(monkeypatch):
    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: None)
    idx = sm.check_index()
    assert idx["available"] is False
    assert idx["indexed_files"] is None
    assert idx["chunks"] is None


def test_check_index_combines_health_and_stats(monkeypatch):
    def fake_get(url, timeout=5):
        if "/health" in url:
            return {"status": "ok", "total_chunks": 2504}
        if "/stats" in url:
            return {"by_category": {"budget": 144, "management": 270}}
        return None

    monkeypatch.setattr(sm, "_http_get_json", fake_get)
    idx = sm.check_index()
    assert idx["available"] is True
    assert idx["chunks"] == 2504
    assert idx["by_category"]["budget"] == 144


def test_check_index_falls_back_to_category_sum_when_no_total(monkeypatch):
    def fake_get(url, timeout=5):
        if "/health" in url:
            return {"status": "ok"}  # no total_chunks/indexed
        if "/stats" in url:
            return {"by_category": {"a": 3, "b": 7}}
        return None

    monkeypatch.setattr(sm, "_http_get_json", fake_get)
    idx = sm.check_index()
    assert idx["chunks"] == 10


# ── search_evidence_for_weaknesses ──────────────────────────────────────

def test_search_evidence_strong_result_classified_pending_review(monkeypatch):
    def fake_post(url, payload, timeout=5):
        return {"results": [{"rank": 1, "score": 0.65, "file_path": "x.md"}]}

    monkeypatch.setattr(sm, "_http_post_json", fake_post)
    weaknesses = [{"id": "weakness-E1", "criterion": "E1", "title": "Objectives under-evidenced"}]
    items = sm.search_evidence_for_weaknesses(weaknesses, search_up=True)
    assert len(items) == 1
    assert items[0]["status"] == "PENDING_REVIEW"
    assert items[0]["evidence_count"] == 1
    assert items[0]["best_score"] == 0.65


def test_search_evidence_weak_result_classified_weak_evidence(monkeypatch):
    def fake_post(url, payload, timeout=5):
        return {"results": [{"rank": 1, "score": 0.25}]}

    monkeypatch.setattr(sm, "_http_post_json", fake_post)
    weaknesses = [{"id": "weakness-IM1", "criterion": "IM1", "title": "Work Plan under-evidenced"}]
    items = sm.search_evidence_for_weaknesses(weaknesses, search_up=True)
    assert items[0]["status"] == "WEAK_EVIDENCE"


def test_search_evidence_no_results_needs_source_doc(monkeypatch):
    monkeypatch.setattr(sm, "_http_post_json", lambda url, payload, timeout=5: {"results": []})
    weaknesses = [{"id": "weakness-IM2IM3", "criterion": "IM2IM3", "title": "Risk under-evidenced"}]
    items = sm.search_evidence_for_weaknesses(weaknesses, search_up=True)
    assert items[0]["status"] == "NEEDS_SOURCE_DOC"
    assert items[0]["best_score"] is None


def test_search_evidence_degrades_when_search_down(monkeypatch):
    def fake_post(url, payload, timeout=5):
        raise AssertionError("must not be called when search_up=False")

    monkeypatch.setattr(sm, "_http_post_json", fake_post)
    weaknesses = [{"id": "weakness-E1", "criterion": "E1", "title": "Objectives under-evidenced"}]
    items = sm.search_evidence_for_weaknesses(weaknesses, search_up=False)
    assert items[0]["status"] == "NEEDS_SOURCE_DOC"
    assert items[0]["evidence_count"] == 0


def test_search_evidence_empty_weaknesses_returns_empty_list(monkeypatch):
    monkeypatch.setattr(sm, "_http_post_json", lambda *a, **k: {"results": []})
    assert sm.search_evidence_for_weaknesses([], search_up=True) == []


# ── run() end-to-end (mocked HTTP) ──────────────────────────────────────

def test_run_writes_state_only_to_state_dir(monkeypatch, fake_repo, state_dir):
    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: None)
    monkeypatch.setattr(sm, "_http_post_json", lambda url, payload, timeout=5: None)

    result = sm.run(fake_repo, state_dir)
    assert result["status"] == "completed"
    assert result["available"] is True
    assert (state_dir / sm.STATE_FILENAME).exists()
    written = json.loads((state_dir / sm.STATE_FILENAME).read_text())
    assert written["services"][0]["status"] == "DOWN"


def test_run_never_touches_proposal_repo(monkeypatch, fake_repo, state_dir):
    import subprocess
    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: None)
    monkeypatch.setattr(sm, "_http_post_json", lambda url, payload, timeout=5: None)

    before = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                             capture_output=True, text=True).stdout
    sm.run(fake_repo, state_dir)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                            capture_output=True, text=True).stdout
    assert before == after == ""


def test_run_reads_weaknesses_from_proposal_intelligence_state(monkeypatch, fake_repo, state_dir):
    pi_state = {
        "weaknesses": [{"id": "weakness-E1", "criterion": "E1", "title": "Objectives under-evidenced"}],
        "fix_packs": [{"id": "fixpack-E1", "status": "PENDING_REVIEW"}],
        "diagnostic_score": {"total": 6.5},
        "overall_status": "BLOCKED",
    }
    (state_dir / sm.PROPOSAL_INTELLIGENCE_FILENAME).write_text(json.dumps(pi_state))

    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: (
        {"status": "ok", "total_chunks": 5} if ":8103" in url else None
    ))
    monkeypatch.setattr(sm, "_http_post_json", lambda url, payload, timeout=5: {
        "results": [{"rank": 1, "score": 0.6}]
    })

    result = sm.run(fake_repo, state_dir)
    assert len(result["evidence_items"]) == 1
    assert result["evidence_items"][0]["weakness_id"] == "weakness-E1"
    assert result["evidence_items"][0]["status"] == "PENDING_REVIEW"
    assert result["fix_packs_summary"]["count"] == 1
    assert result["fix_packs_summary"]["all_pending_review"] is True
    assert result["proposal_intelligence_snapshot"]["diagnostic_score"] == 6.5


def test_run_gracefully_handles_missing_proposal_intelligence_state(monkeypatch, fake_repo, state_dir):
    # No proposal_intelligence_state.json written at all -- run() must
    # not crash, just report zero weaknesses/evidence items.
    monkeypatch.setattr(sm, "_http_get_json", lambda url, timeout=5: None)
    monkeypatch.setattr(sm, "_http_post_json", lambda url, payload, timeout=5: None)

    result = sm.run(fake_repo, state_dir)
    assert result["evidence_items"] == []
    assert result["proposal_intelligence_snapshot"]["available"] is False


def test_chain_reflects_pipeline_state():
    chain_idle = sm._chain(False, False, False, False)
    assert chain_idle[0]["state"] == "idle"

    chain_active = sm._chain(True, True, True, True)
    labels = {c["label"]: c["state"] for c in chain_active}
    assert labels["Agent 5 weakness"] == "ok"
    assert labels["Search evidence"] == "ok"
    assert labels["PENDING_REVIEW"] == "warn"
    assert labels["Human approval"] == "warn"
    assert labels["Apply / Commit + re-eval"] == "idle"
