"""
agents/repository_guardian.py

Ported (not copied) from the old system's guardian agent, used only as
read-only reference. Deterministic, repo-agnostic checks:

- broken `*.md` self-references (backtick-quoted paths that do not
  resolve to a real file, checked by full path and by basename);
- known placeholder/filler artefacts (Lorem ipsum, [TBC], FIXME, etc).

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

PATH_REF_RE = re.compile(r"`([0-9A-Za-z_./-]+\.md)`")


def find_md_files(repo_root: Path) -> list[Path]:
    files = []
    for sub in SCAN_SUBDIRS:
        d = repo_root / sub
        if d.exists():
            files.extend(sorted(d.rglob("*.md")))
    return files


def check_self_references(repo_root: Path, files: list[Path]) -> list[dict]:
    findings = []
    basename_index: dict[str, list[Path]] = {}
    for f in files:
        basename_index.setdefault(f.name, []).append(f)

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            findings.append({"id": f"read-error-{f.name}", "severity": "medium",
                              "title": f"Could not read {f.name}", "description": str(e),
                              "source": str(f.relative_to(repo_root))})
            continue
        seen = set()
        for m in PATH_REF_RE.finditer(text):
            ref = m.group(1)
            if ref in seen:
                continue

            try:
                target = safe_repo_path(repo_root, ref)
            except UnsafeRepositoryPathError:
                # The reference resolves outside the repo root (absolute
                # path, `../` escape, or a symlink escape). Do NOT read
                # it, do NOT even check existence via the raw path --
                # flag it deterministically instead.
                seen.add(ref)
                findings.append({
                    "id": f"unsafe-repository-path-{f.name}-{ref.replace('/', '_')}",
                    "severity": "high",
                    "title": f"Unsafe path reference in {f.name}",
                    "description": (
                        f"References `{ref}`, which resolves outside the repository "
                        f"root (absolute path, `../` escape, or a symlink escape). "
                        f"Not read."
                    ),
                    "source": str(f.relative_to(repo_root)),
                })
                continue

            if target.exists():
                continue
            if ref.split("/")[-1] in basename_index:
                continue
            seen.add(ref)
            findings.append({
                "id": f"broken-self-ref-{f.name}-{ref.replace('/', '_')}",
                "severity": "critical",
                "title": f"Broken self-reference in {f.name}",
                "description": f"References `{ref}`, which does not resolve to any "
                                f"file under the repo root (checked full path and basename).",
                "source": str(f.relative_to(repo_root)),
            })
    return findings


def check_known_artefacts(repo_root: Path, files: list[Path]) -> list[dict]:
    findings = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for pattern, severity, note in KNOWN_ARTEFACTS:
            if re.search(pattern, text):
                findings.append({
                    "id": f"artefact-{f.name}-{pattern[:20]}",
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

    result = {
        **base,
        "status": "completed",
        "repo_root": str(repo_root),
        "scanned_files": len(files),
        "guardian_status": guardian_status,
        "summary": f"Scanned {len(files)} canonical .md files. {len(findings)} finding(s), {len(critical)} critical.",
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
