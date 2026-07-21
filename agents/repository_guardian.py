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

FALSE-POSITIVE RULING (added after a read-only audit found 8 CRITICAL
findings that were all false positives against the real repo). Before
a not-found reference is treated as a genuinely broken self-reference,
three specific, narrow, documented shapes are ruled out -- each still
produces its own finding (nothing is silently dropped), just never at
`critical` severity, so it never escalates project_supervisor's
overall_status:

  1. GENERIC_WP_PLACEHOLDER -- basenames like `WPx_TASKS.md` where
     "x" is a literal template wildcard (never a real WP number), used
     descriptively next to a concrete example (e.g. "... `WPx_TASKS.md`
     derived extract (e.g. `WP2_TASKS.md`)"). Detected by
     `_is_generic_wp_placeholder()`.
  2. EXTERNAL_REPOSITORY_CITATION -- the source text explicitly says
     "external repository" near the reference (e.g. a fuzzing evidence
     record citing `foritech-horizon/foritech-public-evidence`).
     Detected by `_external_repo_citation()` scanning a fixed window of
     text immediately preceding the match. Never resolves the citation
     itself -- only recognises that it was declared external.
  3. SIBLING_WP_REPO_CROSS_REFERENCE -- WP task-description files
     legitimately cite deliverables that live in the six sibling WP
     partner repos (WP1-WP6), not inside FORISEC_REPO_ROOT. This is
     the exact same scope exception already documented and used by
     agents/budget_reader.py for budget figures; `_find_in_sibling_wp_repos()`
     reuses that module's fixed, known repo list (never a directory
     scan) and only ever reads (never writes) those six repos, each
     wrapped in try/except so a missing/renamed sibling repo degrades
     that one lookup to "not found" instead of failing the agent.

NOTE (scope, Phase 1): the original also has a large semantic IP
classification layer. That layer is intentionally NOT ported in this
pass -- it is heuristic/advisory and repo-specific. Flagged explicitly
rather than silently omitted.

Usage: python -m agents.repository_guardian
"""
import os
import re
from pathlib import Path

from agents.budget_reader import DEFAULT_WP_REPOS_ROOT, WP_REPOS
from agents.common import atomic_write_json, base_state, safe_repo_path, UnsafeRepositoryPathError

STATE_FILENAME = "guardian_state.json"

# 03_implementation added after the read-only audit found PM_ALLOCATION.md
# (which genuinely exists at 03_implementation/PM_ALLOCATION.md) was
# invisible to the basename fallback check purely because this directory
# was never scanned -- a tooling gap, not a missing canonical artefact.
SCAN_SUBDIRS = ["00_baseline", "01_work_packages", "02_registers", "03_implementation",
                "docs/technical", "docs/business", "docs/interfaces", "docs/guidance"]

# "WPx" (literal lowercase/uppercase x, never a digit) used as a
# template wildcard placeholder, e.g. `WPx_TASKS.md` next to a concrete
# `WP2_TASKS.md` example. Deliberately narrow -- only matches the exact
# "WP" + non-digit-placeholder-letter + "_" shape, never a real WP1-WP6
# filename.
GENERIC_WP_PLACEHOLDER_RE = re.compile(r"^WP[xX]_")

# A citation is treated as explicitly external if this phrase appears
# in a fixed window of text around the backtick reference. The phrase
# most often trails the reference (e.g. "recorded in `X.md` (external
# repository: ...)"), so the forward window is deliberately larger than
# the backward one -- but both are fixed and small, so an unrelated
# broken reference much earlier or later in the same document is never
# accidentally rescued by a distant, unconnected annotation.
EXTERNAL_REPO_CONTEXT_RE = re.compile(r"external repository", re.IGNORECASE)
EXTERNAL_REPO_CONTEXT_BACKWARD = 80
EXTERNAL_REPO_CONTEXT_FORWARD = 300

_SIBLING_WP_INDEX_CACHE: dict | None = None

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


def _is_generic_wp_placeholder(basename: str) -> bool:
    """True for a literal template-wildcard filename like `WPx_TASKS.md`
    -- never a real file, so never a broken reference. See module
    docstring, ruling 1."""
    return bool(GENERIC_WP_PLACEHOLDER_RE.match(basename))


def _external_repo_citation(text: str, match_start: int, match_end: int) -> bool:
    """True if a fixed, small window immediately around a reference
    explicitly declares it external (e.g. "recorded in `X.md` (external
    repository: `foritech-horizon/foritech-public-evidence`, ...)").
    Only ever looks at that fixed window -- never the whole document --
    so this can't accidentally swallow an unrelated broken reference
    elsewhere in the same file. See module docstring, ruling 2."""
    window = text[max(0, match_start - EXTERNAL_REPO_CONTEXT_BACKWARD):
                  match_end + EXTERNAL_REPO_CONTEXT_FORWARD]
    return bool(EXTERNAL_REPO_CONTEXT_RE.search(window))


def _sibling_wp_repos_root() -> Path:
    return Path(os.environ.get("FORISEC_WP_REPOS_ROOT", str(DEFAULT_WP_REPOS_ROOT)))


def _sibling_wp_basename_index() -> dict:
    """Read-only, fixed-list scan of the six known sibling WP partner
    repos (same list agents/budget_reader.py already uses for budget
    figures) -- never a directory scan, never written to, each repo
    wrapped in try/except so a missing/renamed sibling degrades that
    one repo to 'not found' instead of failing this agent. Cached for
    the lifetime of one process (one `python -m agents.repository_guardian`
    run), never across runs, never persisted."""
    global _SIBLING_WP_INDEX_CACHE
    if _SIBLING_WP_INDEX_CACHE is not None:
        return _SIBLING_WP_INDEX_CACHE

    index: dict = {}
    root = _sibling_wp_repos_root()
    for wp_id, repo_dir_name, _lead in WP_REPOS:
        repo_dir = root / repo_dir_name
        try:
            if not repo_dir.exists():
                continue
            for f in repo_dir.rglob("*.md"):
                index.setdefault(f.name, (wp_id, f))
        except Exception:
            continue  # one unreadable sibling repo must never fail the agent
    _SIBLING_WP_INDEX_CACHE = index
    return index


def _find_in_sibling_wp_repos(basename: str) -> tuple[str, Path] | None:
    """Returns (wp_id, path) if `basename` exists in one of the six
    known sibling WP repos, else None. See module docstring, ruling 3."""
    return _sibling_wp_basename_index().get(basename)


def check_self_references(repo_root: Path, files: list[Path]) -> list[dict]:
    """Returns one finding per distinct canonical_issue_key, each
    carrying every raw occurrence in affected_sources[]."""
    basename_index: dict[str, list[Path]] = {}
    for f in files:
        basename_index.setdefault(f.name, []).append(f)

    # raw_occurrences: canonical_issue_key -> list of (source_rel, raw_ref)
    raw_occurrences: dict[str, list[dict]] = {}
    placeholder_occurrences: dict[str, list[dict]] = {}
    external_occurrences: dict[str, list[dict]] = {}
    cross_repo_occurrences: dict[str, list[dict]] = {}
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

            basename = ref.split("/")[-1]

            # Ruling 1: generic WPx template placeholder -- never a
            # real file, so never a broken reference.
            if _is_generic_wp_placeholder(basename):
                seen_in_file.add(ref)
                key = _canonical_key(ref)
                placeholder_occurrences.setdefault(key, []).append({
                    "source_file": str(f.relative_to(repo_root)),
                    "raw_reference": ref,
                })
                continue

            # Ruling 2: text explicitly declares this an external
            # repository citation immediately before the reference.
            if _external_repo_citation(text, m.start(), m.end()):
                seen_in_file.add(ref)
                key = _canonical_key(ref)
                external_occurrences.setdefault(key, []).append({
                    "source_file": str(f.relative_to(repo_root)),
                    "raw_reference": ref,
                })
                continue

            # Ruling 3: file exists in one of the six known sibling WP
            # partner repos (same documented scope exception as
            # agents/budget_reader.py) -- a legitimate cross-repo
            # citation, not a broken self-reference.
            sibling_hit = _find_in_sibling_wp_repos(basename)
            if sibling_hit is not None:
                wp_id, sibling_path = sibling_hit
                seen_in_file.add(ref)
                key = _canonical_key(ref)
                cross_repo_occurrences.setdefault(key, []).append({
                    "source_file": str(f.relative_to(repo_root)),
                    "raw_reference": ref,
                    "resolved_in": f"{wp_id} ({sibling_path.name})",
                })
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

    for key, occurrences in placeholder_occurrences.items():
        distinct_refs = sorted({o["raw_reference"] for o in occurrences})
        findings.append({
            "finding_id": f"generic-wp-placeholder-{key}",
            "id": f"generic-wp-placeholder-{key}",
            "canonical_issue_key": key,
            "target": key,
            "occurrence_count": len(occurrences),
            "affected_sources": occurrences,
            "severity": "info",
            "title": f"Generic WP-wildcard placeholder (not a real file): {key}",
            "description": (
                f"Referenced as {distinct_refs}; this is a template placeholder "
                f"(literal 'x' standing in for a WP number, always used next to a "
                f"concrete example elsewhere in the same text), never a real filename. "
                f"Never treated as a broken reference."
            ),
            "source": occurrences[0]["source_file"],
        })

    for key, occurrences in external_occurrences.items():
        distinct_refs = sorted({o["raw_reference"] for o in occurrences})
        findings.append({
            "finding_id": f"external-repo-citation-{key}",
            "id": f"external-repo-citation-{key}",
            "canonical_issue_key": key,
            "target": key,
            "occurrence_count": len(occurrences),
            "affected_sources": occurrences,
            "severity": "info",
            "title": f"External repository citation (not a broken self-reference): {key}",
            "description": (
                f"Referenced as {distinct_refs}; the surrounding text explicitly declares "
                f"this an external repository citation. Not resolved against this repo, "
                f"never treated as a broken self-reference."
            ),
            "source": occurrences[0]["source_file"],
        })

    for key, occurrences in cross_repo_occurrences.items():
        distinct_refs = sorted({o["raw_reference"] for o in occurrences})
        resolved_in = sorted({o["resolved_in"] for o in occurrences})
        findings.append({
            "finding_id": f"sibling-wp-repo-citation-{key}",
            "id": f"sibling-wp-repo-citation-{key}",
            "canonical_issue_key": key,
            "target": key,
            "occurrence_count": len(occurrences),
            "affected_sources": occurrences,
            "severity": "info",
            "title": f"Cross-repo citation resolved in sibling WP repo: {key}",
            "description": (
                f"Referenced as {distinct_refs}; found in sibling partner repo(s) "
                f"{resolved_in} (same documented scope exception as "
                f"agents/budget_reader.py). Not present in FORISEC_REPO_ROOT itself, "
                f"never treated as a broken self-reference."
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
