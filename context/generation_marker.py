"""
context/generation_marker.py

PHASE 2 -- final refresh_agents.sh step (12): generation validation and
publish-complete marker.

This is a pure read-only VALIDATION step -- it builds nothing and
writes only one small marker file. It exists so that a genuine failure
partway through generating this refresh cycle's context bundle
(step 10) or context index (step 11) is never silently presented as a
complete, trustworthy generation:

  - Reads project_context_state.json and validates it against
    contracts/project_context_state.schema.json.
  - Reads context.db's meta row.
  - Confirms both are bound to the SAME proposal repo commit (no mixed
    generation -- e.g. step 10 succeeded against commit A but step 11
    then ran against a newer commit B because the repo moved mid-run).
  - Writes FORISEC_STATE_DIR/context_generation_complete.json (atomic)
    with {ok, generation_id, repo_commit, service_commit, checked_at,
    reasons}. If ok=false, the reasons are recorded but exit code is
    still non-zero -- refresh_agents.sh must fail loudly, never publish
    a half generation as if it were complete.

Usage: python -m context.generation_marker
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from agents.common import atomic_write_json, get_repo_commit, read_json_or_none

MARKER_FILENAME = "context_generation_complete.json"


def validate(repo_root: Path, state_dir: Path) -> dict:
    reasons = []

    bundle_path = state_dir / "project_context_state.json"
    bundle = read_json_or_none(bundle_path)
    if bundle is None:
        reasons.append("project_context_state.json missing or invalid JSON.")
    else:
        schema_path = Path(__file__).resolve().parents[1] / "contracts" / "project_context_state.schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.validate(bundle, schema)
        except jsonschema.ValidationError as e:
            reasons.append(f"project_context_state.json failed schema validation: {e.message}")
            bundle = None
        except Exception as e:
            reasons.append(f"Could not load contract schema: {e}")
            bundle = None

    db_path = state_dir / "context" / "context.db"
    db_meta = None
    if not db_path.exists():
        reasons.append("context.db missing.")
    else:
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT proposal_repo_commit, service_commit, generation_id, source_count, chunk_count FROM meta"
            ).fetchone()
            conn.close()
            if row is None:
                reasons.append("context.db meta table is empty.")
            else:
                db_meta = {
                    "proposal_repo_commit": row[0], "service_commit": row[1],
                    "generation_id": row[2], "source_count": row[3], "chunk_count": row[4],
                }
        except Exception as e:
            reasons.append(f"context.db could not be read: {e}")

    if bundle is not None and db_meta is not None:
        if bundle.get("repo_commit") != db_meta["proposal_repo_commit"]:
            reasons.append(
                f"mixed generation: bootstrap repo_commit={bundle.get('repo_commit')} "
                f"!= context.db repo_commit={db_meta['proposal_repo_commit']}"
            )

    live_commit = get_repo_commit(repo_root)
    if live_commit is None:
        reasons.append("Could not determine live proposal repo commit.")
    elif bundle is not None and bundle.get("repo_commit") != live_commit:
        reasons.append(f"bootstrap repo_commit={bundle.get('repo_commit')} does not match live HEAD={live_commit}.")

    ok = not reasons
    result = {
        "ok": ok,
        "generation_id": (bundle or {}).get("generation_id") or (db_meta or {}).get("generation_id"),
        "repo_commit": (bundle or {}).get("repo_commit") or (db_meta or {}).get("proposal_repo_commit"),
        "service_commit": (bundle or {}).get("service_commit") or (db_meta or {}).get("service_commit"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
    }
    atomic_write_json(state_dir / MARKER_FILENAME, result)
    return result


def main():
    repo_root = Path(os.environ.get("FORISEC_REPO_ROOT", "")).expanduser()
    state_dir = Path(os.environ.get("FORISEC_STATE_DIR", "")).expanduser()
    if not repo_root or not state_dir:
        print("[generation_marker] CONFIG ERROR: FORISEC_REPO_ROOT and FORISEC_STATE_DIR must be set.")
        sys.exit(1)
    result = validate(repo_root, state_dir)
    if result["ok"]:
        print(f"[generation_marker] OK -- generation={result['generation_id']} repo_commit={result['repo_commit']}")
        sys.exit(0)
    else:
        print(f"[generation_marker] GENERATION INVALID -- reasons: {result['reasons']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
