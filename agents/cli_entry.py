"""
agents/cli_entry.py

Shared CLI bootstrap for all four agents. Reads FORISEC_REPO_ROOT and
FORISEC_STATE_DIR directly (does not import app/, keeping agents/
usable standalone via `python -m agents.<name>`). Fails loudly on
missing config -- no fallback to any old-repo path.
"""
import os
import sys
from pathlib import Path


def _require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        print(f"[forisec-cl3-dashboard] CONFIG ERROR: {name} is not set. "
              f"Refusing to guess or fall back to any old-repo path.",
              file=sys.stderr)
        sys.exit(1)
    return Path(value).expanduser().resolve()


def run_agent_cli(run_fn, agent_name: str) -> None:
    repo_root = _require_env("FORISEC_REPO_ROOT")
    state_dir = _require_env("FORISEC_STATE_DIR")

    if not repo_root.exists():
        print(f"[forisec-cl3-dashboard] CONFIG ERROR: FORISEC_REPO_ROOT does not exist: {repo_root}",
              file=sys.stderr)
        sys.exit(1)

    try:
        state_dir.resolve().relative_to(repo_root.resolve())
        print(f"[forisec-cl3-dashboard] CONFIG ERROR: FORISEC_STATE_DIR ({state_dir}) "
              f"may not be inside FORISEC_REPO_ROOT ({repo_root}).", file=sys.stderr)
        sys.exit(1)
    except ValueError:
        pass

    state_dir.mkdir(parents=True, exist_ok=True)

    result = run_fn(repo_root, state_dir)
    status = result.get("status", "unknown")
    print(f"[{agent_name}] {status} -- state written to {state_dir}")
    if status == "failed":
        sys.exit(2)
