"""
app/config.py

The ONLY place environment variables are read. No fallback to any old
system / old-repo path is permitted: missing configuration must fail
loudly, never silently default to a path belonging to the old system.
"""
import os
import sys
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. This service refuses to guess or fall "
            f"back to any old-repo path. Set {name} explicitly (see .env.example)."
        )
    return Path(value).expanduser().resolve()


# Known old-system installation root. Only used for a defensive check --
# never for a fallback default.
_OLD_SYSTEM_ROOT = Path("/home/forybg/services/foritech-os").resolve()


def _is_inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_config(create_state_dir: bool = False) -> dict:
    """
    Validates configuration only. By default (create_state_dir=False)
    this performs NO filesystem writes -- importing app.main must never
    create directories as a side effect. Only CLI agent entrypoints
    (agents/cli_entry.py) pass create_state_dir=True, and only right
    before they are about to write their own state file.
    """
    repo_root = _require_env("FORISEC_REPO_ROOT")
    state_dir = _require_env("FORISEC_STATE_DIR")

    if not repo_root.exists():
        raise ConfigError(f"FORISEC_REPO_ROOT does not exist: {repo_root}")
    if not (repo_root / ".git").exists():
        raise ConfigError(
            f"FORISEC_REPO_ROOT ({repo_root}) does not look like a Git "
            f"repository (no .git/ found)."
        )

    if _is_inside(state_dir, repo_root):
        raise ConfigError(
            f"FORISEC_STATE_DIR ({state_dir}) may not be inside "
            f"FORISEC_REPO_ROOT ({repo_root})."
        )
    if _is_inside(state_dir, _OLD_SYSTEM_ROOT):
        raise ConfigError(
            f"FORISEC_STATE_DIR ({state_dir}) may not be inside the old "
            f"system root ({_OLD_SYSTEM_ROOT})."
        )

    if create_state_dir:
        state_dir.mkdir(parents=True, exist_ok=True)  # no sudo required, user-owned path

    return {"repo_root": repo_root, "state_dir": state_dir}


def load_config_or_exit() -> dict:
    try:
        return load_config()
    except ConfigError as e:
        print(f"[forisec-cl3-dashboard] CONFIG ERROR: {e}", file=sys.stderr)
        sys.exit(1)
