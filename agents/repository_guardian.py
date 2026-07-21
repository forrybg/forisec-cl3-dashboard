"""
agents/repository_guardian.py

Ported (not copied) from the old system's guardian agent, used only as
read-only reference. Deterministic, repo-agnostic checks:

- broken `*.md` self-references (backtick-quoted paths that do not
  resolve to a real file, checked by full path and by basename);
- known placeholder/filler artefacts (Lorem ipsum, [TBC], FIXME, etc).

Deduplication (STEP 1 OF 2, evidence-pipeline pass): one broken TARGET
file is one logical problem, however many places reference it and
however the reference is spelled (relative vs. prefixed path). Each
finding therefore carries `canonical_issue_key` (the resolved
basename), `occurrence_count`, and `affected_sources[]` -- no
occurrence information is dropped, it is grouped instead of repeated
as separate critical rows. `severity` is the max severity across all
occurrences of that key.

NOTE (scope, Phase 1): the original also has a large semantic IP
classification layer. That layer is intentionally NOT ported in this
pass -- it is heuristic/advisory and repo-specific. Flagged explicitly
rather than silently omitted.

Usage: python -m agents.repository_guardian
"""
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state, safe_repo_path, UnsafeRepositoryPathError

STATE_FILENAME = "guardian_state.json"

SCAN_SUBDIRS = ["00_baseline", "01_work_packages", "02_registers",
                "docs/technical", "docs/business", "docs/interfaces", "docs/guidance"]

KNOWN_ARTEFACTS = [
    (r"Final Portal legal data", "high", "Recurring template placeholder -- must be replaced."),
    (r"\bLorem ipsum\b", "high", "Lorem-ipsum filler text left in a canonical document."),
    (r"\[TBC\]", "low", "Bracketed TBC placeholder -- confirm intentional vs. forgotten."),
    (r"\bFIXME\b", "medium", "Explicit FIXME marker left in canonical text."),
]

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

PATH_REF_RE = re.compile(r"`([0-9A-Za-z_./-]+\.md)`")


def find_md_files(repo_root: Path) -> list[Path]:
    files = []
    for sub in SCAN_SUBDIRS:
        d = repo_root / sub
        if d.exists():
            files.extend(sorted(d.rglob("*.md")))
    return files


def _canonical_key(ref: str) -> str:
    """Normalize a broken reference to the resolved basename. Two
    different spellings of a path that both end in the same filename
    (e.g. `WP3_X.md` and `00_baseline/WP3_X.md`) are the same logical
    missing document."""
    return ref.rsplit("/", 1)[-1]


def check_self_references(repo_root: Path, files: list[Path]) -> list[dict]:
    """Returns one finding per distinct canonical_issue_key, each
    carrying every raw occurrence in affected_sources[]."""
    basename_index: dict[str, list[Path]] = {}
    for f in files:
        basename_index.setdefault(f.name, []).append(f)

    # raw_occurrences: canonical_issue_key -> list of (source_rel, raw_ref)
    raw_occurrences: dict[str, list[dict]] = {}
    unsafe_findings: list[dict] = []

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            unsafe_findings.append({
                "finding_id": f"read-error-{f.name}", "canonical_issue_key": f"read-error-{f.name}",
                "target": f.name, "occurrence_count": 1,
                "affected_sources": [{"source_file": str(f.relative_to(repo_root)), "raw_reference": None}],
                "severity": "medium", "title": f"Could not read {f.name}", "description": str(e),
                "id": f"read-error-{f.name}", "source": str(f.relative_to(repo_root)),
            })
            continue

        seen_in_file = set()
        for m in PATH_REF_RE.finditer(text):
            ref = m.group(1)
            if ref in seen_in_file:
                continue

            try:
                target = safe_repo_path(repo_root, ref)
            except UnsafeRepositoryPathError:
                seen_in_file.add(ref)
                key = f"unsafe-repository-path-{ref.replace('/', '_')}"
                unsafe_findings.append({
                    "finding_id": key, "canonical_issue_key": key, "target": ref, "occurrence_count": 1,
                    "affected_sources": [{"source_file": str(f.relative_to(repo_root)), "raw_reference": ref}],
                    "severity": "high",
                    "title": f"Unsafe path reference in {f.name}",
                    "description": (
                        f"References `{ref}`, which resolves outside the repository "
                        f"root (absolute path, `../` escape, or a symlink escape). Not read."
                    ),
                    "id": key, "source": str(f.relative_to(repo_root)),
                })
                continue

            if target.exists():
                continue
            if ref.split("/")[-1] in basename_index:
                continue
            seen_in_file.add(ref)
            key = _canonical_key(ref)
            raw_occurrences.setdefault(key, []).append({
                "source_file": str(f.relative_to(repo_root)),
                "raw_reference": ref,
            })

    findings = []
    for key, occurrences in raw_occurrences.items():
        distinct_refs = sorted({o["raw_reference"] for o in occurrences})
        findings.append({
            "finding_id": f"broken-self-ref-{key}",
            "id": f"broken-self-ref-{key}",  # backward-compatible alias
            "canonical_issue_key": key,
            "target": key,
            "occurrence_count": len(occurrences),
            "affected_sources": occurrences,
            "severity": "critical",
            "title": f"Broken self-reference: {key}",
            "description": (
                f"Referenced as {distinct_refs} from {len(occurrences)} location(s) across "
                f"{len({o['source_file'] for o in occurrences})} file(s); does not resolve to any "
                f"file under the repo root (checked full path and basename)."
            ),
            "source": occurrences[0]["source_file"],
        })
    return unsafe_findings + findings


def check_known_artefacts(repo_root: Path, files: list[Path]) -> list[dict]:
    # Grouped the same way: one canonical_issue_key per (pattern, file) --
    # artefacts are inherently per-file style issues, not cross-file broken
    # links, so no further cross-file grouping is meaningful here.
    findings = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for pattern, severity, note in KNOWN_ARTEFACTS:
            if re.search(pattern, text):
                key = f"artefact-{f.name}-{pattern[:20]}"
                findings.append({
                    "finding_id": key, "id": key, "canonical_issue_key": key,
                    "target": str(f.relative_to(repo_root)), "occurrence_count": 1,
                    "affected_sources": [{"source_file": str(f.relative_to(repo_root)), "raw_reference": None}],
                    "severity": severity,
                    "title": f"Placeholder artefact in {f.name}",
                    "description": note,
                    "source": str(f.relative_to(repo_root)),
                })
    return findings


def run(repo_root: Path, state_dir: Path) -> dict:
    base = base_state("repository_guardian", repo_root)
    files = find_md_files(repo_root)

    findings = check_self_references(repo_root, files) + check_known_artefacts(repo_root, files)
    critical = [f for f in findings if f["severity"] == "critical"]
    guardian_status = "FAIL" if critical else ("WARN" if findings else "PASS")

    total_occurrences = sum(f.get("occurrence_count", 1) for f in findings)

    result = {
        **base,
        "status": "completed",
        "repo_root": str(repo_root),
        "scanned_files": len(files),
        "guardian_status": guardian_status,
        "summary": f"Scanned {len(files)} canonical .md files. {len(findings)} distinct issue(s) "
                   f"({total_occurrences} occurrence(s) total), {len(critical)} critical.",
        "findings": findings,
        "ip_classification": {
            "status": "NOT_EVALUATED",
            "note": "Semantic IP-classification layer not ported in Phase 1 (deterministic checks only).",
        },
        "warnings": [],
        "errors": [],
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "repository_guardian")


if __name__ == "__main__":
    main()
