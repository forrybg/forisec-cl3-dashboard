"""
context/identity.py

Single, fixed source of truth for THIS project's identity. These
values are Python constants -- never read from
config/canonical_documents.json (whose own "project" field is only a
short display label, "FORISEC", and must never be used alone as a
unique identifier: multiple Foritech systems could plausibly reuse
that same short label). Every context.db row, API/MCP response, and
generation-marker record for this project must carry, and be checked
against, exactly these values.

Three systems exist and must never be confused with one another:
  - forisec-cl3-2026        = THIS project (the current Horizon
                               CL3 proposal, topic HORIZON-CL3-2026-02-
                               CS-ECCC-01). This codebase.
  - foritech-os              = a different, legacy control-plane/
                               dashboard codebase.
  - foritech-secure-system   = Foritech's own product repository.

Any context.db, bootstrap bundle, or retrieval result whose stored
project_id/context_namespace does not match PROJECT_ID/
CONTEXT_NAMESPACE below must be treated as foreign/untrusted and
rejected -- see context/retrieval.py's _envelope() and
context/generation_marker.py's validate().
"""
from __future__ import annotations

PROJECT_ID = "forisec-cl3-2026"
PROJECT_SHORT_NAME = "FORISEC"
PROJECT_DISPLAY_NAME = "FORISEC — HORIZON-CL3-2026-02-CS-ECCC-01"
CONTEXT_NAMESPACE = "forisec_cl3_2026"

# Fixed MCP tool name prefix. Every tool this project exposes over the
# foritech-server-readonly connector uses this prefix so a brand-new
# chat can never mistake it for a same-shaped tool belonging to a
# different project/namespace (e.g. a hypothetical future
# foritech_os_context_bootstrap or foritech_secure_system_context_*).
MCP_TOOL_PREFIX = "forisec_cl3_2026_context_"


def identity_matches(project_id, context_namespace) -> bool:
    """True only if BOTH fields exactly match this project's fixed
    constants. Callers must reject (not merely warn about) any
    context.db meta row, bootstrap bundle, or retrieval payload for
    which this returns False -- never fall back to treating a partial
    or single-field match as good enough, and never accept the bare
    short label "FORISEC" alone as if it were project_id."""
    return project_id == PROJECT_ID and context_namespace == CONTEXT_NAMESPACE
