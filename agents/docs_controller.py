"""
agents/docs_controller.py

Ported (not copied) from the old system's docs-controller agent, used
only as read-only reference. Manifest-driven canonical-document status
scanner for the FORISEC proposal repository. Read-only against the
repo; writes only to FORISEC_STATE_DIR/docs_state.json.

Usage: python -m agents.docs_controller
"""
import json
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state

STATUS_MARKER_RE = re.compile(
    r"<!--\s*CANONICAL_STATUS:\s*(\w+)\s*"
    r"(?:\|\s*VERSION:\s*([\w.]+)\s*)?"
    r"(?:\|\s*LAST_REVIEWED:\s*([\d-]+)\s*)?-->"
)

VALID_STATUSES = {
    "NOT_STARTED", "DRAFT", "REVIEW_REQUIRED", "FROZEN",
    "EVIDENCE_REQUIRED", "BLOCKED", "NOT_APPLICABLE_YET", "SUPERSEDED",
}

STATE_FILENAME = "docs_state.json"


def load_manifest(repo_root: Path) -> dict:
    manifest_path = repo_root / "config" / "canonical_documents.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def phase_is_active(phase_name, phases, frozen_or_draft, doc_by_phase_and_required):
    phase = phases.get(phase_name, {})
    for dep_phase in phase.get("depends_on", []):
        for p in doc_by_phase_and_required.get(dep_phase, []):
            if p not in frozen_or_draft:
                return False
    return True


def read_file_status(repo_root: Path, rel_path: str) -> dict:
    """0-byte / whitespace-only files count as NOT existing (scaffold
    placeholders, not real content)."""
    full = repo_root / rel_path
    if not full.exists():
        return {"exists": False, "marker_status": None, "version": None, "last_reviewed": None}
    try:
        text = full.read_text(encoding="utf-8")
    except Exception:
        return {"exists": True, "marker_status": None, "version": None, "last_reviewed": None}
    if len(text.strip()) == 0:
        return {"exists": False, "marker_status": None, "version": None, "last_reviewed": None}
    m = STATUS_MARKER_RE.search(text)
    if not m:
        return {"exists": True, "marker_status": None, "version": None, "last_reviewed": None}
    return {"exists": True, "marker_status": m.group(1).upper(),
            "version": m.group(2), "last_reviewed": m.group(3)}


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("docs_controller", repo_root)

    if not repo_root.exists():
        result = {**base, "status": "failed",
                  "errors": [{"type": "missing_input", "message": f"Repo root not found: {repo_root}"}]}
        atomic_write_json(state_dir / STATE_FILENAME, result)
        return result

    try:
        manifest = load_manifest(repo_root)
    except Exception as e:
        result = {**base, "status": "failed",
                  "errors": [{"type": "missing_manifest", "message": str(e)}]}
        atomic_write_json(state_dir / STATE_FILENAME, result)
        return result

    phases = manifest.get("phases", {})
    docs = manifest.get("documents", [])

    raw = {d["path"]: read_file_status(repo_root, d["path"]) for d in docs}
    frozen_or_draft = {
        d["path"] for d in docs
        if raw[d["path"]]["exists"] and raw[d["path"]]["marker_status"] in (None, "FROZEN", "DRAFT")
    }
    doc_by_phase_and_required = {}
    for d in docs:
        if d.get("required", True):
            doc_by_phase_and_required.setdefault(d["required_phase"], []).append(d["path"])

    documents_out, findings = [], []
    counts = {s: 0 for s in VALID_STATUSES}
    path_index = {d["path"] for d in docs}

    for d in docs:
        path = d["path"]
        r = raw[path]
        phase_active = phase_is_active(d["required_phase"], phases, frozen_or_draft, doc_by_phase_and_required)
        blocked_by = [dep for dep in d.get("dependencies", [])
                      if dep in path_index and dep not in frozen_or_draft]

        if not r["exists"]:
            status = "NOT_APPLICABLE_YET" if not phase_active else ("BLOCKED" if blocked_by else "NOT_STARTED")
        else:
            marker = r["marker_status"]
            if marker == "FROZEN":
                status = "FROZEN"
            elif marker == "DRAFT":
                status = "DRAFT"
            elif marker in ("REVIEW_REQUIRED", "EVIDENCE_REQUIRED", "BLOCKED", "SUPERSEDED", "NOT_APPLICABLE_YET"):
                status = marker
            elif marker is None:
                status = "DRAFT"
                findings.append({"id": f"no-status-marker-{path.replace('/', '_')}", "severity": "low",
                                  "title": f"No CANONICAL_STATUS marker in {path}",
                                  "description": "File exists but has no machine-readable status marker; treated as DRAFT.",
                                  "source": path})
            else:
                status = "REVIEW_REQUIRED"
                findings.append({"id": f"unknown-marker-{path.replace('/', '_')}", "severity": "medium",
                                  "title": f"Unknown CANONICAL_STATUS value in {path}",
                                  "description": f"Marker says '{marker}', not in the recognised vocabulary.",
                                  "source": path})
            if status == "FROZEN" and blocked_by:
                findings.append({"id": f"frozen-but-blocked-{path.replace('/', '_')}", "severity": "critical",
                                  "title": f"{path} marked FROZEN but a dependency is not ready",
                                  "description": f"Depends on {blocked_by}, not yet FROZEN/DRAFT.",
                                  "source": path})

        if status not in VALID_STATUSES:
            status = "REVIEW_REQUIRED"
        counts[status] += 1
        documents_out.append({
            "path": path, "title": d.get("title", path), "required_phase": d.get("required_phase"),
            "required": d.get("required", True), "status": status,
            "version": r["version"], "last_reviewed": r["last_reviewed"],
            "dependencies": d.get("dependencies", []),
        })

    critical_findings = [f for f in findings if f["severity"] == "critical"]
    if critical_findings:
        overall_status = "FAIL"
    elif counts["BLOCKED"]:
        overall_status = "WARN"
    elif counts["DRAFT"] or counts["NOT_STARTED"] or counts["REVIEW_REQUIRED"] or counts["EVIDENCE_REQUIRED"]:
        overall_status = "ON_TRACK_WITH_DRAFTS"
    else:
        overall_status = "ON_TRACK"

    result = {
        **base,
        "status": "completed",
        "target_repo": str(repo_root),
        "manifest_version": manifest.get("manifest_version"),
        "current_phase": manifest.get("current_phase"),
        "overall_status": overall_status,
        "planned_count": len(docs),
        "frozen_count": counts["FROZEN"],
        "draft_count": counts["DRAFT"],
        "missing_count": counts["NOT_STARTED"],
        "blocked_count": counts["BLOCKED"],
        "not_applicable_yet_count": counts["NOT_APPLICABLE_YET"],
        "documents": documents_out,
        "findings": findings,
        "warnings": [],
        "errors": [],
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "docs_controller")


if __name__ == "__main__":
    main()
