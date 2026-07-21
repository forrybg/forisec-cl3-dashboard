"""
pipeline/context_builder.py

PHASE 1 -- Project Context Bundle.

Deterministic, read-only builder that assembles a compact bootstrap
document (project_context_state.json) for a brand-new FORISEC chat,
so it can learn project identity, architecture, WP/partner structure,
budget status, recent decisions, blockers, completed work, forbidden
changes and next actions WITHOUT re-reading every canonical document
from scratch.

This is a pipeline component (like pipeline/evidence_assembler.py),
not an LLM call and not a 7th scoring agent. It never invents a fact:
every field is either copied/summarized from an already-written state
file, extracted deterministically (regex/heading-scan) from a fixed,
named canonical document, or a hardcoded constant documented in this
module (constraints/forbidden_changes). Anything it cannot determine
is written as UNKNOWN/UNAVAILABLE, never guessed.

READS ONLY (nothing else):
  Proposal repo (FORISEC_REPO_ROOT), read-only:
    - config/canonical_documents.json      (via agents.docs_controller.load_manifest)
    - 00_baseline/FORITECH_SYSTEM_INDEX.md (heading titles only, never full body)
    - 99_decisions/DECISION_LOG.md         (per-ISS summaries only, never full log)
    - `git -C <repo_root> rev-parse --short HEAD`               (fixed, safe)
    - `git -C <repo_root> log -n <N> --pretty=... --date=short` (fixed, safe)
  This service's own state files under FORISEC_STATE_DIR:
    - docs_state.json
    - budget_state.json
    - guardian_state.json
    - supervisor_state.json
    - proposal_evidence_state.json
    - proposal_intelligence_state.json
  This service's own repo (for service_commit), read-only:
    - `git -C <service_repo_root> rev-parse --short HEAD`

WRITES ONLY:
  FORISEC_STATE_DIR/project_context_state.json (atomic write, via
  agents.common.atomic_write_json). Never writes to the proposal repo,
  never writes to this service's own repo, never touches foritech-os,
  never reads or migrates the old memory.db.

FRESHNESS RULE (see contracts/project_context_state.schema.json):
  - UNAVAILABLE: the live proposal repo commit could not be determined
    at all, OR the canonical_documents.json manifest itself could not
    be read (a foundational input for canonical_sources).
  - STALE: repo commit is known, but at least one consumed state file
    is missing, unreadable/invalid JSON, or was generated against a
    different commit than the live proposal repo HEAD (mixed-commit
    input). A bundle is never presented as FRESH under any of these
    conditions.
  - FRESH: repo commit is known and every consumed state file is
    present, valid, and stamped with that exact same commit.

Usage: python -m pipeline.context_builder
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents.common import atomic_write_json, read_json_or_none
from agents.docs_controller import load_manifest

STATE_FILENAME = "project_context_state.json"
SCHEMA_VERSION = "1.0"
CONTEXT_MODEL_VERSION = "2.0"

# Consumed state files -- filenames only, never imported from agents/
# (this module reads their JSON output, it does not run them).
CONSUMED_STATE_FILES = {
    "docs": "docs_state.json",
    "budget": "budget_state.json",
    "guardian": "guardian_state.json",
    "supervisor": "supervisor_state.json",
    "evidence": "proposal_evidence_state.json",
    "proposal_intelligence": "proposal_intelligence_state.json",
}

SYSTEM_INDEX_REL_PATH = "00_baseline/FORITECH_SYSTEM_INDEX.md"
DECISION_LOG_REL_PATH = "99_decisions/DECISION_LOG.md"
DELIVERABLE_REGISTER_REL_PATH = "02_registers/DELIVERABLE_REGISTER.md"

# PHASE 2 -- fixed, explicit WP1-WP6 mapping (never derived from a
# directory scan). One entry per real Work Package document.
WP_DOCUMENT_MAPPING = [
    ("WP1", "01_work_packages/WP1_PROJECT_MANAGEMENT.md"),
    ("WP2", "01_work_packages/WP2_PQC_PLATFORM.md"),
    ("WP3", "01_work_packages/WP3_EMBEDDED_PROVENANCE.md"),
    ("WP4", "01_work_packages/WP4_FPGA_HARDWARE_SECURITY.md"),
    ("WP5", "01_work_packages/WP5_OPERATIONAL_PILOT.md"),
    ("WP6", "01_work_packages/WP6_EXPLOITATION_STANDARDISATION.md"),
]

WP_TITLE_RE = re.compile(r"^#\s+(WP\d+.*)$", re.MULTILINE)
WP_LEAD_RE = re.compile(r"^\*\*Lead:\*\*\s*(.+?)\s*$", re.MULTILINE)
WP_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def _wp_task_heading_re(wp_number: str) -> re.Pattern:
    return re.compile(rf"^## T{wp_number}\.\d+", re.MULTILINE)


def _wp_other_reference_re(wp_number: str) -> re.Pattern:
    return re.compile("\\bWP(?!" + re.escape(wp_number) + "\\b)([1-6])\\b")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

DECISION_BLOCK_RE = re.compile(r"^## (ISS-\d+)\s*(?:—|--)?\s*(.*)$", re.MULTILINE)
DECISION_DATE_RE = re.compile(r"^\*\*Date:\*\*\s*(.+)$", re.MULTILINE)
DECISION_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.MULTILINE)

# Section headings that may carry the "question/decision being raised"
# vs. the "conclusion/finding reached" -- checked in this preference
# order, first match wins. Never invents a summary if none is present.
QUESTION_HEADINGS = ("### Question", "### Decision")
CONCLUSION_HEADINGS = ("### Conclusion", "### Finding", "### Decision")
FOLLOWUP_HEADINGS = ("### Follow-up action", "### Follow-up")

SUMMARY_MAX_CHARS = 110  # PHASE 2: lowered from 180 after WP1-WP6 mapping pushed bootstrap over 6000 tokens; keeps hard cap with margin

MAX_SERVICE_COMMITS = 4
MAX_PROPOSAL_COMMITS = 3

CONVENTIONAL_PREFIXES = ("feat", "fix", "docs", "chore", "test", "refactor", "perf", "build", "ci")

# Fixed, hardcoded constants -- documented here, never derived from any
# file. These mirror the standing engineering constraints repeatedly
# imposed on this service across every prior implementation task.
CONSTRAINTS = [
    "This service must never import or depend on foritech-os at runtime (verified by tests/test_isolation.py).",
    "app/ (dashboard/API layer) must never import agents/ or write state -- it only reads JSON already written to FORISEC_STATE_DIR.",
    "All agents and pipeline components are read-only against FORISEC_REPO_ROOT -- state is written only to FORISEC_STATE_DIR.",
    "Agent 5 scoring is evidence-gated (SCORING_MODEL_VERSION 2.0) -- no keyword/word-count scoring, no automatic 5/5 without a semantic evaluator.",
    "Guardian findings are deduplicated by canonical_issue_key; project_supervisor tracks freshness and result as two independent axes, never conflated.",
    "This context bundle (PHASE 1) is deterministic and read-only -- no SQLite, embeddings, semantic search, or MCP connector until a separately authorized PHASE 2.",
]

FORBIDDEN_CHANGES = [
    "Do not modify the proposal repository (forisec-cl3-2026) from this service.",
    "Do not modify the old foritech-os installation.",
    "Do not read or migrate the old memory.db.",
    "Do not change the Agent 5 scoring formula without an explicit, separate task.",
    "Do not change Repository Guardian's core broken-reference rules without an explicit, separate task.",
    "Do not modify Caddy, systemd units, or production environment files from this service.",
    "Do not restart production services as a side effect of a read/build operation.",
]


# ── git metadata (fixed, safe commands only) ────────────────────────────

def _git_short_head(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def _git_recent_commits(repo_root: Path, n: int) -> list[dict]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", f"-n{n}",
             "--pretty=format:%h%x1f%ad%x1f%s", "--date=short"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return []
        commits = []
        for line in out.stdout.strip().split("\n"):
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            commit_hash, date, subject = parts
            commits.append({"commit": commit_hash, "date": date, "subject": subject})
        return commits
    except Exception:
        return []


def _classify_commit_subject(subject: str) -> str:
    """Conventional-commit-prefix classification only (feat/fix/docs/
    chore/...). Never an interpretation of what the commit *means* --
    just the declared category, or 'other' if it doesn't match."""
    m = re.match(r"^(\w+)(?:\([^)]*\))?!?:", subject)
    if m and m.group(1).lower() in CONVENTIONAL_PREFIXES:
        return m.group(1).lower()
    return "other"


# ── canonical document extraction (headings/summaries only) ────────────

def _extract_section_titles(text: str) -> list[str]:
    return [m.group(2).strip() for m in HEADING_RE.finditer(text) if m.group(2).strip()]


def _first_paragraph(text: str, start: int) -> str:
    """From `start` (just after a heading line), return the first
    non-empty paragraph, truncated to SUMMARY_MAX_CHARS. Stops at the
    next heading or blank-line-terminated paragraph, whichever is
    first."""
    rest = text[start:]
    next_heading = HEADING_RE.search(rest)
    window = rest[:next_heading.start()] if next_heading else rest
    paragraph = window.strip().split("\n\n", 1)[0].strip()
    paragraph = re.sub(r"\s+", " ", paragraph)
    if len(paragraph) > SUMMARY_MAX_CHARS:
        paragraph = paragraph[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return paragraph or None


def _find_section(block_text: str, headings: tuple[str, ...]) -> str | None:
    for heading in headings:
        idx = block_text.find(heading)
        if idx != -1:
            return _first_paragraph(block_text, idx + len(heading))
    return None


def _parse_decision_log(text: str) -> list[dict]:
    matches = list(DECISION_BLOCK_RE.finditer(text))
    decisions = []
    for i, m in enumerate(matches):
        decision_id = m.group(1)
        title = m.group(2).strip()
        block_start = m.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[block_start:block_end]

        date_m = DECISION_DATE_RE.search(block)
        status_m = DECISION_STATUS_RE.search(block)

        decisions.append({
            "id": decision_id,
            "title": title,
            "date": date_m.group(1).strip() if date_m else None,
            "status": status_m.group(1).strip() if status_m else None,
            "question_summary": _find_section(block, QUESTION_HEADINGS),
            "conclusion_summary": _find_section(block, CONCLUSION_HEADINGS),
            "follow_up": _find_section(block, FOLLOWUP_HEADINGS),
            "source_path": f"{DECISION_LOG_REL_PATH}#{decision_id}",
        })
    return decisions


def _classify_decisions(decisions: list[dict]) -> tuple[list, list, list]:
    """Returns (open, recent, superseded). A decision is 'superseded'
    only if its own Status field starts with SUPERSEDED (never inferred
    from prose); 'open' only if Status starts with OPEN/PENDING/
    IN_PROGRESS/UNRESOLVED; everything else (including RESOLVED, or a
    genuinely missing/unknown status) is 'recent'."""
    open_d, recent_d, superseded_d = [], [], []
    for d in decisions:
        status = (d.get("status") or "").strip().upper()
        if status.startswith("SUPERSEDED"):
            superseded_d.append(d)
        elif status.startswith(("OPEN", "PENDING", "IN_PROGRESS", "UNRESOLVED")):
            open_d.append(d)
        else:
            recent_d.append(d)
    return open_d, recent_d, superseded_d


# ── state file loading (missing/invalid never crashes the builder) ─────

def _load_state(state_dir: Path, filename: str) -> tuple[dict | None, str]:
    """Returns (data_or_None, status) where status is one of
    'ok' / 'missing' / 'invalid'."""
    path = state_dir / filename
    if not path.exists():
        return None, "missing"
    data = read_json_or_none(path)
    if data is None or not isinstance(data, dict):
        return None, "invalid"
    return data, "ok"


# ── deterministic derived sections ──────────────────────────────────────

def _parse_deliverable_register_for_wp(text: str, wp_number: str) -> list[str]:
    """Deterministic Markdown table-row scan: one row per deliverable,
    `| D{n}.x | Title | Lead | T{n}.y[, ...] | ... |`. Only rows whose
    deliverable code's WP number matches are kept. Never invents a
    deliverable that isn't a literal table row in the real register."""
    row_re = re.compile(r"^\|\s*(D" + re.escape(wp_number) + r"\.\d+)\s*\|", re.MULTILINE)
    return list(dict.fromkeys(m.group(1) for m in row_re.finditer(text)))


def _first_wp_section_paragraph(text: str) -> str | None:
    """First paragraph under the WP document's own '## 1. ...' role/
    scope section -- a deterministic heading-anchored extraction, never
    a free-form summary."""
    heading_re = re.compile(r"^## 1\..*$", re.MULTILINE)
    m = heading_re.search(text)
    if not m:
        return None
    return _first_paragraph(text, m.end())


def _work_package_summary_v2(repo_root: Path) -> list[dict]:
    """Real WP1-WP6 breakdown (fixes the PHASE 1 limitation, which
    mistakenly used the six evaluation criteria E1/E2/I1/I2I3/IM1/IM2IM3
    as a stand-in for work packages). Deterministic heading/table
    parsing only -- UNKNOWN wherever a fact cannot be safely extracted,
    never a generated guess."""
    deliverable_text = None
    deliverable_path = repo_root / DELIVERABLE_REGISTER_REL_PATH
    if deliverable_path.exists():
        try:
            deliverable_text = deliverable_path.read_text(encoding="utf-8")
        except Exception:
            deliverable_text = None

    out = []
    for wp_id, rel_path in WP_DOCUMENT_MAPPING:
        wp_number = wp_id[2:]
        entry = {
            "wp_id": wp_id, "title": "UNKNOWN", "lead": "UNKNOWN",
            "purpose_summary": None, "task_count": 0,
            "deliverable_references": [], "dependencies": [],
            "status": "UNKNOWN", "source_path": rel_path, "available": False,
        }
        wp_path = repo_root / rel_path
        if not wp_path.exists():
            out.append(entry)
            continue
        try:
            text = wp_path.read_text(encoding="utf-8")
        except Exception:
            out.append(entry)
            continue

        entry["available"] = True
        title_m = WP_TITLE_RE.search(text)
        if title_m:
            entry["title"] = title_m.group(1).strip()
        lead_m = WP_LEAD_RE.search(text)
        if lead_m:
            entry["lead"] = lead_m.group(1).strip()
        status_m = WP_STATUS_RE.search(text)
        if status_m:
            entry["status"] = status_m.group(1).strip()
        entry["purpose_summary"] = _first_wp_section_paragraph(text)
        entry["task_count"] = len(_wp_task_heading_re(wp_number).findall(text))
        entry["dependencies"] = sorted({
            f"WP{m.group(1)}" for m in _wp_other_reference_re(wp_number).finditer(text)
        })
        if deliverable_text:
            entry["deliverable_references"] = _parse_deliverable_register_for_wp(deliverable_text, wp_number)
        out.append(entry)
    return out


PARTNER_SUMMARY_FIELDS = (
    "name", "role", "wp_responsibility", "profile_status", "pic_status",
    "commitment_status", "personnel_status", "budget_status", "freshness", "result",
)


def _partner_summary(evidence: dict | None) -> list[dict]:
    """A direct field-for-field copy of a fixed, named subset of
    partner_readiness -- never reinterpreted, just narrower than the
    full record (drops verbose evidence_files/missing_fields arrays)
    to keep the bundle within its bootstrap token budget."""
    if not evidence:
        return []
    partners = []
    for p in evidence.get("partner_readiness", []):
        entry = {field: p.get(field) for field in PARTNER_SUMMARY_FIELDS}
        entry["source_path"] = CONSUMED_STATE_FILES["evidence"]
        partners.append(entry)
    return partners


def _budget_summary(budget: dict | None) -> dict:
    if not budget:
        return {"available": False, "reason": "budget_state.json missing or invalid"}
    return {
        "available": bool(budget.get("available")),
        "total_pm": budget.get("total_pm"),
        "total_eur": budget.get("total_eur"),
        "any_missing": budget.get("any_missing"),
        "source_path": CONSUMED_STATE_FILES["budget"],
    }


def _evidence_summary(evidence: dict | None) -> dict:
    if not evidence:
        return {"available": False, "reason": "proposal_evidence_state.json missing or invalid"}
    return {
        "available": True,
        "freshness": evidence.get("freshness"),
        "result": evidence.get("result"),
        "coverage_summary": evidence.get("coverage_summary"),
        "source_path": CONSUMED_STATE_FILES["evidence"],
    }


def _evaluation_summary(pi: dict | None) -> dict:
    if not pi:
        return {"available": False, "reason": "proposal_intelligence_state.json missing or invalid"}
    ca = pi.get("competitive_assessment") or {}
    return {
        "available": True,
        "diagnostic_score": pi.get("diagnostic_score"),
        "fundability": pi.get("fundability"),
        "overall_status": pi.get("overall_status"),
        "competitive_score": ca.get("score"),
        "competitive_label": ca.get("label"),
        "source_path": CONSUMED_STATE_FILES["proposal_intelligence"],
    }


def _critical_findings(guardian: dict | None, evidence: dict | None) -> list[dict]:
    findings = []
    seen_keys = set()
    for f in (guardian or {}).get("findings", []):
        if f.get("severity") != "critical":
            continue
        key = f.get("canonical_issue_key") or f.get("id") or f.get("title")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        findings.append({
            "canonical_issue_key": key,
            "severity": "critical",
            "title": f.get("title", ""),
            "source_path": CONSUMED_STATE_FILES["guardian"],
        })
    for c in (evidence or {}).get("contradictions", []):
        if c.get("severity") != "critical":
            continue
        key = c.get("id") or c.get("claim_source") or c.get("reason")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        findings.append({
            "canonical_issue_key": key,
            "severity": "critical",
            "title": c.get("reason") or c.get("claim") or "",
            "source_path": CONSUMED_STATE_FILES["evidence"],
        })
    return findings


def _current_state(producers: dict[str, tuple[dict | None, str]], live_repo_commit: str | None,
                    supervisor: dict | None) -> dict:
    out_producers = []
    for name, (data, status) in producers.items():
        filename = CONSUMED_STATE_FILES[name]
        if status != "ok":
            out_producers.append({
                "name": name, "source_path": filename, "available": False,
                "freshness": "UNAVAILABLE", "result": None,
            })
            continue
        recorded_commit = data.get("repo_commit")
        freshness = "UNKNOWN"
        if live_repo_commit and recorded_commit:
            freshness = "FRESH" if live_repo_commit == recorded_commit else "STALE"
        result = (
            data.get("guardian_status") or data.get("overall_status")
            or data.get("status")
        )
        out_producers.append({
            "name": name, "source_path": filename, "available": True,
            "freshness": freshness, "result": result,
        })
    overall = supervisor.get("overall_status") if supervisor else "UNKNOWN"
    return {"overall_status": overall or "UNKNOWN", "producers": out_producers}


def _next_actions(evidence: dict | None, critical_findings: list[dict], docs: dict | None,
                   evaluation_summary: dict) -> list[str]:
    actions = []
    if critical_findings:
        actions.append(
            f"Resolve {len(critical_findings)} critical Guardian/evidence finding(s) before promotion."
        )
    if evidence:
        missing_partner = sum(
            1 for p in evidence.get("partner_readiness", [])
            if p.get("profile_status") == "PROFILE_MISSING"
        )
        if missing_partner:
            actions.append(f"Complete {missing_partner} partner profile(s) under 05_partners/.")
        for m in evidence.get("missing_evidence", []) if isinstance(evidence.get("missing_evidence"), list) else []:
            if m.get("key") == "pm_allocation":
                actions.append("Populate 03_implementation/PM_ALLOCATION.md.")
                break
    if docs:
        review_required = docs.get("draft_count", 0)  # documented count field, not re-derived
        blocked = docs.get("blocked_count", 0)
        if blocked:
            actions.append(f"Resolve {blocked} blocked canonical document(s).")
    if evaluation_summary.get("overall_status") == "BLOCKED":
        actions.append("Address blocking criteria before this proposal can be promoted.")
    if not actions:
        actions.append("No deterministic next actions identified from current state.")
    return actions


# ── freshness determination ─────────────────────────────────────────────

def _determine_freshness(manifest_ok: bool, live_repo_commit: str | None,
                          producers: dict[str, tuple[dict | None, str]]) -> str:
    if not manifest_ok or live_repo_commit is None:
        return "UNAVAILABLE"
    for _name, (data, status) in producers.items():
        if status != "ok":
            return "STALE"
        if data.get("repo_commit") != live_repo_commit:
            return "STALE"
    return "FRESH"


def _token_estimate(bundle_without_estimate: dict) -> dict:
    serialized = json.dumps(bundle_without_estimate, ensure_ascii=False)
    characters = len(serialized)
    return {
        "characters": characters,
        "estimated_tokens": round(characters / 4),
        "method": "characters/4 heuristic (no tokenizer dependency)",
    }


# ── main entrypoint ──────────────────────────────────────────────────────

def run(repo_root: Path, state_dir: Path, service_repo_root: Path | None = None) -> dict:
    if service_repo_root is None:
        service_repo_root = Path(__file__).resolve().parents[1]

    live_repo_commit = _git_short_head(repo_root)
    service_commit = _git_short_head(service_repo_root)

    try:
        manifest = load_manifest(repo_root)
        manifest_ok = True
    except Exception:
        manifest = {}
        manifest_ok = False

    canonical_sources = [
        {
            "path": d.get("path"),
            "title": d.get("title"),
            "required_phase": d.get("required_phase"),
            "required": bool(d.get("required", False)),
        }
        for d in manifest.get("documents", [])
    ] if manifest_ok else []

    system_index_path = repo_root / SYSTEM_INDEX_REL_PATH
    if system_index_path.exists():
        try:
            idx_text = system_index_path.read_text(encoding="utf-8")
            architecture_summary = {
                "source_path": SYSTEM_INDEX_REL_PATH,
                "section_titles": _extract_section_titles(idx_text),
                "available": True,
            }
        except Exception:
            architecture_summary = {"source_path": SYSTEM_INDEX_REL_PATH, "section_titles": [], "available": False}
    else:
        architecture_summary = {"source_path": SYSTEM_INDEX_REL_PATH, "section_titles": [], "available": False}

    decision_log_path = repo_root / DECISION_LOG_REL_PATH
    if decision_log_path.exists():
        try:
            decisions = _parse_decision_log(decision_log_path.read_text(encoding="utf-8"))
        except Exception:
            decisions = []
    else:
        decisions = []
    open_decisions, recent_decisions, superseded_decisions = _classify_decisions(decisions)

    producers: dict[str, tuple[dict | None, str]] = {
        name: _load_state(state_dir, filename) for name, filename in CONSUMED_STATE_FILES.items()
    }
    docs, _ = producers["docs"]
    budget, _ = producers["budget"]
    guardian, _ = producers["guardian"]
    supervisor, _ = producers["supervisor"]
    evidence, _ = producers["evidence"]
    proposal_intelligence, _ = producers["proposal_intelligence"]

    evaluation_summary = _evaluation_summary(proposal_intelligence)
    critical = _critical_findings(guardian, evidence)

    proposal_completed_work = [
        {**c, "category": _classify_commit_subject(c["subject"]), "source": "forisec-cl3-2026 git log", "repo": "proposal"}
        for c in _git_recent_commits(repo_root, MAX_PROPOSAL_COMMITS)
    ]
    service_completed_work = [
        {**c, "category": _classify_commit_subject(c["subject"]), "source": "forisec-cl3-dashboard git log", "repo": "service"}
        for c in _git_recent_commits(service_repo_root, MAX_SERVICE_COMMITS)
    ]
    # Backwards-compatible aggregate (PHASE 1 shape) -- PHASE 2 consumers
    # should prefer the two split fields above.
    completed_work = service_completed_work + proposal_completed_work

    freshness = _determine_freshness(manifest_ok, live_repo_commit, producers)

    # canonical_sources already lists every canonical document's own
    # path -- not repeated here. retrieval_index covers the *other*
    # inputs this bundle summarizes: state files, the two hand-parsed
    # documents, and one pointer per individual decision.
    retrieval_index = [{"label": name, "source_path": filename} for name, filename in CONSUMED_STATE_FILES.items()]
    retrieval_index.append({"label": "canonical_documents_manifest", "source_path": "config/canonical_documents.json"})
    retrieval_index.append({"label": "system_index", "source_path": SYSTEM_INDEX_REL_PATH})
    retrieval_index.append({"label": "decision_log", "source_path": DECISION_LOG_REL_PATH})
    retrieval_index += [{"label": d["id"], "source_path": d["source_path"]} for d in decisions]

    source_map = {
        "canonical_sources": ["config/canonical_documents.json"],
        "architecture_summary": [SYSTEM_INDEX_REL_PATH],
        "current_state": list(CONSUMED_STATE_FILES.values()),
        "work_package_summary": [rel_path for _wp_id, rel_path in WP_DOCUMENT_MAPPING] + [DELIVERABLE_REGISTER_REL_PATH],
        "partner_summary": [CONSUMED_STATE_FILES["evidence"]],
        "budget_summary": [CONSUMED_STATE_FILES["budget"]],
        "evidence_summary": [CONSUMED_STATE_FILES["evidence"]],
        "evaluation_summary": [CONSUMED_STATE_FILES["proposal_intelligence"]],
        "critical_findings": [CONSUMED_STATE_FILES["guardian"], CONSUMED_STATE_FILES["evidence"]],
        "open_decisions": [DECISION_LOG_REL_PATH],
        "recent_decisions": [DECISION_LOG_REL_PATH],
        "superseded_decisions": [DECISION_LOG_REL_PATH],
        "completed_work": ["forisec-cl3-dashboard git log", "forisec-cl3-2026 git log"],
        "proposal_completed_work": ["forisec-cl3-2026 git log"],
        "service_completed_work": ["forisec-cl3-dashboard git log"],
    }

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "context_model_version": CONTEXT_MODEL_VERSION,
        "project_id": manifest.get("project") or "UNKNOWN",
        "project_title": manifest.get("project") or "UNKNOWN",
        "topic_id": manifest.get("topic_id") or "UNKNOWN",
        "repo_commit": live_repo_commit,
        "service_commit": service_commit,
        "generation_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "freshness": freshness,
        "canonical_sources": canonical_sources,
        "architecture_summary": architecture_summary,
        "current_state": _current_state(producers, live_repo_commit, supervisor),
        "work_package_summary": _work_package_summary_v2(repo_root),
        "partner_summary": _partner_summary(evidence),
        "budget_summary": _budget_summary(budget),
        "evidence_summary": _evidence_summary(evidence),
        "evaluation_summary": evaluation_summary,
        "critical_findings": critical,
        "open_decisions": open_decisions,
        "recent_decisions": recent_decisions,
        "completed_work": completed_work,
        "proposal_completed_work": proposal_completed_work,
        "service_completed_work": service_completed_work,
        "superseded_decisions": superseded_decisions,
        "constraints": CONSTRAINTS,
        "forbidden_changes": FORBIDDEN_CHANGES,
        "next_actions": _next_actions(evidence, critical, docs, evaluation_summary),
        "retrieval_index": retrieval_index,
        "source_map": source_map,
    }
    bundle["token_estimate"] = _token_estimate(bundle)

    atomic_write_json(state_dir / STATE_FILENAME, bundle)
    return bundle


def main():
    repo_root = Path(os.environ.get("FORISEC_REPO_ROOT", "")).expanduser()
    state_dir = Path(os.environ.get("FORISEC_STATE_DIR", "")).expanduser()
    if not repo_root or not state_dir:
        print("[context_builder] CONFIG ERROR: FORISEC_REPO_ROOT and FORISEC_STATE_DIR must be set.")
        raise SystemExit(1)
    state_dir.mkdir(parents=True, exist_ok=True)
    result = run(repo_root, state_dir)
    print(f"[context_builder] completed -- freshness={result['freshness']} "
          f"tokens~={result['token_estimate']['estimated_tokens']} -- state written to {state_dir}")


if __name__ == "__main__":
    main()
