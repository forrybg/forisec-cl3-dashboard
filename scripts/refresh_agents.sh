#!/bin/bash
# scripts/refresh_agents.sh
#
# Runs the four FORISEC CL3 agents in sequence. Fail-fast on a genuine
# execution error (missing manifest, missing repo, etc. -- each
# agent's CLI entrypoint exits non-zero only for status=="failed").
#
# A diagnostic FAIL/CRITICAL result from repository_guardian or
# project_supervisor is NOT an execution error -- as long as the
# agent's own state JSON was written successfully, its CLI exits 0.
# This script must not be "fixed" to swallow real execution errors --
# it relies on that existing exit-code contract in agents/cli_entry.py.
#
# Requires FORISEC_REPO_ROOT and FORISEC_STATE_DIR to already be set
# in the environment (e.g. via systemd EnvironmentFile= or a sourced
# /etc/forisec-cl3-dashboard/environment). This script does not set
# any fallback values itself.
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

echo "[refresh_agents] proposal_evaluator..."
"$PYTHON" -m agents.proposal_evaluator

echo "[refresh_agents] repository_guardian..."
"$PYTHON" -m agents.repository_guardian

echo "[refresh_agents] project_supervisor..."
"$PYTHON" -m agents.project_supervisor

echo "[refresh_agents] all four agents completed."
