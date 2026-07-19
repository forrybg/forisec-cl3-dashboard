# forisec-cl3-dashboard

Fully standalone dashboard/agent service for the FORISEC
`HORIZON-CL3-2026-02-CS-ECCC-01` proposal repository.

## Architectural boundary

Deliberately isolated from `foritech-os` (the old dashboard/agent
system) and from the proposal repository itself:

- **No imports from `foritech-os`** anywhere in `app/` or `agents/`.
- **No reads** of the old `canonical/` or old `server/state/` paths.
- The **proposal source repository is read-only** — this service never
  writes into it (enforced by tests, see `tests/test_isolation.py`).
- **All runtime state** lives under `FORISEC_STATE_DIR`, never inside
  the proposal repo, never inside `foritech-os`.
- Importing `app.main` performs config **validation** only — it never
  creates directories or files as a side effect. Only the agent CLI
  entrypoints create the state directory, and only right before they
  write their own state file.

## Ports

- Old dashboard: `127.0.0.1:8765` — untouched by this service.
- This service (development): `127.0.0.1:8766`.
- Production deployment (systemd unit, Caddy route): **Phase 2B —
  pending**, not yet configured.

## Environment variables

Both are required; the service fails loudly (non-zero exit / 500 at
startup) if either is missing — it never falls back to an old-repo
path.

| Variable | Meaning |
|---|---|
| `FORISEC_REPO_ROOT` | Absolute path to the `forisec-cl3-2026` proposal repo (must exist and contain `.git/`) |
| `FORISEC_STATE_DIR` | Absolute path for this service's own JSON state (must not be inside `FORISEC_REPO_ROOT` or inside the old `foritech-os` root) |

See `.env.example` for development defaults.

## Agent status

| Agent | Purpose | Status |
|---|---|---|
| `docs_controller` | Manifest-driven canonical document status scan | Implemented |
| `proposal_evaluator` | Activation gate for Excellence/Impact/Implementation review | Implemented — **scoring itself is NOT IMPLEMENTED**. `score` is always `null`; the dashboard renders this as "SCORING NOT IMPLEMENTED" / "SCORE UNAVAILABLE", never as a successful evaluation. |
| `repository_guardian` | Deterministic broken-reference / placeholder-artefact scan | Implemented (semantic IP-classification layer intentionally not ported yet) |
| `project_supervisor` | Aggregates the three state files; precedence `CRITICAL > DEGRADED > REVIEW > OK` | Implemented |

## Run agents (writes only to `FORISEC_STATE_DIR`)

    export FORISEC_REPO_ROOT=/path/to/forisec-cl3-2026
    export FORISEC_STATE_DIR=/path/to/state/dir
    python -m agents.docs_controller
    python -m agents.proposal_evaluator
    python -m agents.repository_guardian
    python -m agents.project_supervisor

## Run the dashboard (development)

    uvicorn app.main:app --host 127.0.0.1 --port 8766

## Tests

    python -m pytest -q

## Development setup

    python3 -m venv .venv
    .venv/bin/pip install fastapi uvicorn jinja2 jsonschema pytest httpx
