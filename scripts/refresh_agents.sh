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
#   8. decision_log           -- unaffected by this step, kept last
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

echo "[refresh_agents] decision_log..."
"$PYTHON" -m agents.decision_log

echo "[refresh_agents] all agents + evidence pipeline completed."
