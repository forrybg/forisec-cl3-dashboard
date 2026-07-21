#!/bin/bash
# scripts/refresh_agents.sh
#
# Runs the FORISEC CL3 agents + evidence pipeline in sequence. Fail-fast
# on a genuine execution error (missing manifest, missing repo, etc. --
# each agent's CLI entrypoint exits non-zero only for status=="failed").
#
# A diagnostic FAIL/CRITICAL/BLOCKED result from repository_guardian,
# project_supervisor, evidence_assembler, or proposal_intelligence is
# NOT an execution error -- as long as the state JSON was written
# successfully, the CLI exits 0. This script must not be "fixed" to
# swallow real execution errors -- it relies on the existing exit-code
# contract in agents/cli_entry.py.
#
# Production order (STEP 1 OF 2 -- connect evidence pipeline):
#   1. docs_controller        -- canonical document status
#   2. budget_reader          -- real WP budget/PM figures (was orphaned;
#                                 now runs before anything that depends on it)
#   3. proposal_evaluator     -- activation gate only, no score
#   4. repository_guardian    -- broken references / placeholder artefacts
#   5. project_supervisor     -- watches docs/evaluation/guardian/budget state
#   6. evidence_assembler     -- normalizes all of the above into one bundle
#                                 (read-only pipeline component, not a 6th agent)
#   7. proposal_intelligence  -- diagnostic score (unchanged in this step)
#   8. service_monitor        -- read-only HTTP status of sibling GPU/search
#                                 services (8101-8103) + live search evidence
#                                 for open weaknesses; never blocks the
#                                 pipeline if those services are down
#   9. decision_log           -- unaffected by this step, kept last among agents
#  10. context_builder        -- PHASE 1 Project Context Bundle
#                                 (project_context_state.json); reads the
#                                 state files written by steps 1-9 plus a
#                                 fixed set of canonical proposal documents
#                                 and safe git metadata.
#  11. context.index_builder  -- PHASE 2 project-scoped SQLite context.db
#                                 (FTS5 + optional semantic embeddings via
#                                 the stateless :8101 worker); atomic clean
#                                 rebuild into context.db.tmp-<generation>
#                                 then os.replace(); never touches the old
#                                 foritech-os memory.db/index.db.
#  12. context.generation_marker -- validates that project_context_state.json
#                                 (step 10) and context.db (step 11) are
#                                 schema-valid AND bound to the SAME proposal
#                                 repo commit (no mixed generation), then
#                                 writes context_generation_complete.json.
#                                 Always the LAST project-context producer
#                                 in this script.
#
# FAILURE CONTRACT FOR STEPS 10-12: a non-zero exit from context_builder,
# context.index_builder, or context.generation_marker must fail this whole
# script (set -e already enforces this). Because each of the three writes
# its own output atomically and only replaces the previous valid file on
# success, a failure at step 10 or 11 leaves the last good
# project_context_state.json / context.db / context_generation_complete.json
# untouched and readable -- the dashboard's /health/ready endpoint will
# report readiness as STALE/DEGRADED based on that leftover marker's
# repo_commit rather than silently serving a half-built generation as
# complete. This script itself never "publishes" a generation by any
# separate step; publication is simply "the marker file says ok:true and
# matches the live repo commit," which only happens if 10, 11, and 12 all
# succeeded in the same run.
#
# NOTE ON ATOMICITY: each step's own state file is written atomically
# (temp file + os.replace, see agents/common.py::atomic_write_json).
# This script does NOT make the run AS A WHOLE transactionally atomic --
# if it fails partway through, some state files will reflect a newer
# run than others until the next successful pass. Only the write of any
# single state file (including project_context_state.json) is atomic.
#
# Requires FORISEC_REPO_ROOT and FORISEC_STATE_DIR to already be set
# in the environment (e.g. via systemd EnvironmentFile= or a sourced
# /etc/forisec-cl3-dashboard/environment). This script does not set
# any fallback values itself. Timer cadence is unchanged by this script.
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${SERVICE_DIR}/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "[refresh_agents] ERROR: ${PYTHON} not found or not executable." >&2
    exit 1
fi

cd "$SERVICE_DIR"

echo "[refresh_agents] docs_controller..."
"$PYTHON" -m agents.docs_controller

echo "[refresh_agents] budget_reader..."
"$PYTHON" -m agents.budget_reader

echo "[refresh_agents] proposal_evaluator..."
"$PYTHON" -m agents.proposal_evaluator

echo "[refresh_agents] repository_guardian..."
"$PYTHON" -m agents.repository_guardian

echo "[refresh_agents] project_supervisor..."
"$PYTHON" -m agents.project_supervisor

echo "[refresh_agents] evidence_assembler..."
"$PYTHON" -m pipeline.evidence_assembler

echo "[refresh_agents] proposal_intelligence..."
"$PYTHON" -m agents.proposal_intelligence

echo "[refresh_agents] service_monitor..."
"$PYTHON" -m agents.service_monitor

echo "[refresh_agents] decision_log..."
"$PYTHON" -m agents.decision_log

echo "[refresh_agents] context_builder..."
"$PYTHON" -m pipeline.context_builder

echo "[refresh_agents] context.index_builder..."
"$PYTHON" -m context.index_builder

echo "[refresh_agents] context.generation_marker..."
"$PYTHON" -m context.generation_marker

echo "[refresh_agents] all agents + evidence pipeline completed."
