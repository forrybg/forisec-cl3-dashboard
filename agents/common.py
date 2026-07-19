"""
agents/common.py

Shared, repo-agnostic helpers for all four agents. Deliberately
independent of the old system -- no imports from it anywhere in this
package.
"""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"


class UnsafeRepositoryPathError(Exception):
    """
    Raised when a repository-controlled path reference (from a
    manifest, a Markdown cross-reference, or any other repo-content
    source) would escape the repository root -- via an absolute path,
    an empty reference, a `../` traversal, or a symlink that resolves
    outside the repo.

    This is a project-specific exception, not a generic ValueError, so
    callers can catch it precisely and turn it into a deterministic
    finding instead of letting a path-traversal attempt propagate as
    an uncontrolled crash or, worse, a silent read of an external file.
    """


def safe_repo_path(repo_root: Path, candidate: str | Path) -> Path:
    """
    Resolve `candidate` (a repository-relative path reference coming
    from repo-controlled content: a manifest entry, a Markdown
    backtick-reference, etc.) safely against `repo_root`.

    Never creates a file or directory. Never falls back to cwd or any
    other repository. Raises UnsafeRepositoryPathError instead of
    returning a path outside repo_root.
    """
    repo_root_resolved = Path(repo_root).resolve(strict=False)

    if candidate is None or str(candidate).strip() == "":
        raise UnsafeRepositoryPathError("Empty path reference is not allowed.")

    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        raise UnsafeRepositoryPathError(
            f"Absolute path reference is not allowed: {candidate!r}"
        )

    # Joining onto the resolved repo root and resolving again follows
    # any symlink components (without creating anything on disk) and
    # normalises any `../` segments, so the escape check below sees the
    # *real* target, not just the literal textual path.
    resolved = (repo_root_resolved / candidate_path).resolve(strict=False)

    if not resolved.is_relative_to(repo_root_resolved):
        raise UnsafeRepositoryPathError(
            f"Path reference escapes the repository root: "
            f"{candidate!r} resolved to {resolved}, outside {repo_root_resolved}"
        )

    return resolved


def get_repo_commit(repo_root: Path) -> str | None:
    """Read-only: never mutates the proposal repository."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: dict) -> None:
    """
    Write JSON atomically: temp file in the same directory, fsync,
    then os.replace. Never partially-written state, never a write
    outside `path.parent`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # best-effort; some filesystems/containers disallow fsync
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def read_json_or_none(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def base_state(agent_id: str, repo_root: Path) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "agent_id": agent_id,
        "repo_commit": get_repo_commit(repo_root),
        "run_timestamp": now_iso(),
    }
