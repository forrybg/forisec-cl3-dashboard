"""
agents/budget_reader.py

Deterministic, read-only parser that extracts each WP's own budget
breakdown (PM total, EUR grand total, confirmation status) from the
per-WP README.md files, and writes budget_state.json.

SCOPE EXCEPTION (documented, deliberate): every other agent in this
package is confined to FORISEC_REPO_ROOT (forisec-cl3-2026 only) --
that is the hard isolation rule for this dashboard. Budget figures,
however, live in six SEPARATE git repositories (WP1-WP6), siblings of
forisec-cl3-2026 under the same parent directory, not inside it. This
agent is the one deliberate, narrowly-scoped exception: it reads only
a fixed list of six known README.md files, each wrapped in its own
try/except so a missing or renamed WP repo degrades that one row to
"unavailable" instead of failing the whole agent. It does not read,
write, or expand into anything else in the WP repos, and it never
writes to them.

If FORISEC_WP_REPOS_ROOT is set in the environment, it is used instead
of the hardcoded default below -- this lets the exception become
properly configured later without changing this file again.

Usage: python -m agents.budget_reader
"""
import os
import re
from pathlib import Path

from agents.common import atomic_write_json, base_state

STATE_FILENAME = "budget_state.json"

DEFAULT_WP_REPOS_ROOT = Path("/home/forybg/code/HORIZON-CL3-2026-02-CS-ECCC-01")

# (wp_id, repo_dir_name, lead) -- fixed, known list; not derived from
# directory scanning, so an unrelated directory can never be picked up.
WP_REPOS = [
    ("WP1", "WP1_FORITECH_PROJECT_MANAGEMEN", "Foritech"),
    ("WP2", "WP2_FORITECH_PQC_PLATFORM", "Foritech"),
    ("WP3", "WP3_RTU_EMBEDDED_PROVENANCE", "RTU"),
    ("WP4", "WP4_Logiicdev_FPGA_HARDWARE_SECURITY", "Logiicdev"),
    ("WP5", "WP5_SOLARIX_OPERATIONAL_PILOT", "SOLARIX"),
    ("WP6", "WP6_FORITECH_EXPLOITATION_STANDARDISATION", "Foritech"),
]

# "**Total personnel (A)**| ... | **47 PM** | ... | **€276,680**" style rows,
# tolerant of extra bold markers/columns between the PM and EUR figures.
TOTAL_PERSONNEL_RE = re.compile(
    r"\*\*Total personnel \(A\)\*\*.*?\*\*(\d+)\s*PM\*\*.*?\*\*€\s?([\d,]+(?:\.\d+)?)\*\*",
    re.IGNORECASE,
)

# Grand-total row variants seen across the six READMEs:
#   "**Foritech WP1 = (A+C) × 1.25** | **€355,850**"
#   "**F = (A+C) × 1.25** | **€422,847.70**"
#   "**Final WP4 budget** | **€720,710**"
GRAND_TOTAL_RES = [
    re.compile(r"\*\*(?:Foritech\s+WP\d.*?=|F\s*=)\s*\(A\+C\)\s*×\s*1\.25\*\*\s*\|\s*\*\*€\s?([\d,]+(?:\.\d+)?)\*\*", re.IGNORECASE),
    re.compile(r"\*\*Final\s+WP\d\s+budget\*\*\s*\|\s*\*\*€\s?([\d,]+(?:\.\d+)?)\*\*", re.IGNORECASE),
]

# Header-line confirmation status, e.g. "Budget status — 🟢 CONFIRMED (...)"
# or "⚠️ NOT YET CONFIRMED by partner".
STATUS_RE = re.compile(r"Budget status\b.*?\b([A-Z]{2,}(?:\s+[A-Z]{2,}){0,4})\b")


def _to_number(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_wp_readme(text: str) -> dict:
    pm = None
    personnel_total = None
    m = TOTAL_PERSONNEL_RE.search(text)
    if m:
        pm = int(m.group(1))
        personnel_total = _to_number(m.group(2))

    grand_total = None
    grand_total_is_draft = False
    for rx in GRAND_TOTAL_RES:
        gm = rx.search(text)
        if gm:
            grand_total = _to_number(gm.group(1))
            # If the matched grand-total row's own text says "old task set"
            # (e.g. WP2's superseded T2.x figure), flag it -- don't silently
            # present a stale figure as current.
            grand_total_is_draft = "old task set" in gm.group(0).lower()
            break

    status = None
    sm = STATUS_RE.search(text)
    if sm:
        status = sm.group(1).strip()

    return {"pm": pm, "personnel_total_eur": personnel_total,
            "grand_total_eur": grand_total, "grand_total_is_draft": grand_total_is_draft,
            "status": status}


def run(repo_root: Path, state_dir: Path) -> dict:
    # NOTE: `repo_root` here is FORISEC_REPO_ROOT (forisec-cl3-2026), used
    # only for base_state()'s repo_commit stamp -- NOT for locating the
    # WP READMEs, per the documented scope exception above.
    base = base_state("budget_reader", repo_root)

    wp_repos_root = Path(os.environ.get("FORISEC_WP_REPOS_ROOT", str(DEFAULT_WP_REPOS_ROOT)))

    rows = []
    total_pm = 0
    total_eur = 0.0
    any_missing = False

    for wp_id, dirname, lead in WP_REPOS:
        readme_path = wp_repos_root / dirname / "README.md"
        if not readme_path.exists():
            rows.append({"wp": wp_id, "lead": lead, "available": False,
                         "reason": f"{readme_path} not found"})
            any_missing = True
            continue
        try:
            text = readme_path.read_text(encoding="utf-8")
            parsed = parse_wp_readme(text)
            parsed.update({"wp": wp_id, "lead": lead, "available": True})
            rows.append(parsed)
            if parsed["pm"]:
                total_pm += parsed["pm"]
            if parsed["grand_total_eur"]:
                total_eur += parsed["grand_total_eur"]
        except Exception as e:
            rows.append({"wp": wp_id, "lead": lead, "available": False,
                         "reason": f"parse error: {e}"})
            any_missing = True

    result = {
        **base,
        "status": "completed",
        "available": True,
        "source": f"{wp_repos_root}/<WP repo>/README.md (six fixed repos, scope exception documented in this file's docstring)",
        "rows": rows,
        "total_pm": total_pm,
        "total_eur": round(total_eur, 2),
        "any_missing": any_missing,
        "summary": f"Parsed {sum(1 for r in rows if r.get('available'))}/6 WP READMEs. "
                   f"Total {total_pm} PM, EUR {total_eur:,.0f} (as currently drafted, not consortium-reconciled).",
    }
    atomic_write_json(state_dir / STATE_FILENAME, result)
    return result


def main():
    from .cli_entry import run_agent_cli
    run_agent_cli(run, "budget_reader")


if __name__ == "__main__":
    main()
