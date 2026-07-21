"""
Guards the isolation contract: this service must never depend on
foritech-os, the old dashboard, or old canonical/server-state paths.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ["app", "agents", "pipeline"]

# Per-file exceptions: a literal reference is allowed ONLY where it is
# used purely as a defensive negative-check constant (e.g. "state dir
# must NOT be inside this path"), never as an import, a fallback
# default, or a read target.
ALLOWLIST = {
    "app/config.py": {"foritech-os"},  # _OLD_SYSTEM_ROOT defensive check only
    # Explanatory docstring only (scope-exception rationale for its
    # read-only HTTP health/search calls to sibling loopback services).
    # No import, no file path, no fallback default -- verified by the
    # AST import-scan test below, which still enforces the real rule.
    "agents/service_monitor.py": {"foritech-os"},
}

FORBIDDEN_SUBSTRINGS = [
    "foritech-os",
    "foritech_os",
    "dashboard.app.dashboard",
    "from dashboard import",
    "import dashboard",
    "canonical/readiness_score",
    "server/state/new-repo",
    "server/state/supervisor",
    "MemorySystem",
    "agent5_eval",
    "agent0_supervisor",
]

ALLOWED_TOP_LEVEL_IMPORTS = {
    "app", "agents", "pipeline", "fastapi", "starlette", "jinja2", "jsonschema",
    "json", "os", "sys", "re", "subprocess", "tempfile", "pathlib",
    "datetime", "typing",
    # urllib (stdlib) is required by agents/service_monitor.py's read-only
    # HTTP health/search calls to the sibling foritech-* GPU/search
    # services (loopback ports 8101-8103). This is a plain stdlib HTTP
    # client, never a foritech-os import -- see that file's module
    # docstring for the full scope-exception rationale.
    "urllib",
}


def _all_source_files():
    files = []
    for d in SOURCE_DIRS:
        files.extend((PROJECT_ROOT / d).rglob("*.py"))
    return files


def test_no_forbidden_references_in_source():
    offenders = []
    for f in _all_source_files():
        rel = str(f.relative_to(PROJECT_ROOT))
        allowed = ALLOWLIST.get(rel, set())
        text = f.read_text(encoding="utf-8")
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in allowed:
                continue
            if bad in text:
                offenders.append((rel, bad))
    assert offenders == [], f"Forbidden references found: {offenders}"


def test_ast_no_forbidden_imports():
    """
    AST-based check (stronger than plain substring matching): parses
    every import statement in app/ and agents/ and asserts none of them
    references anything outside this project. Guards against a
    substring scan giving a false pass on an unusual import formatting.
    """
    offenders = []
    for f in _all_source_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in ALLOWED_TOP_LEVEL_IMPORTS:
                        offenders.append((str(f.relative_to(PROJECT_ROOT)), alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # relative import within this package -- fine
                if node.module:
                    top = node.module.split(".")[0]
                    if top not in ALLOWED_TOP_LEVEL_IMPORTS:
                        offenders.append((str(f.relative_to(PROJECT_ROOT)), node.module))
    assert offenders == [], f"Unexpected imports found via AST scan: {offenders}"


def test_dashboard_does_not_import_agents():
    """
    The dashboard (app/) must never import agents/ -- it only reads the
    JSON state agents already produced. This guarantees a GET request
    can never trigger an agent run or a repo write.
    """
    app_files = list((PROJECT_ROOT / "app").rglob("*.py"))
    offenders = []
    for f in app_files:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "agents":
                        offenders.append(str(f.relative_to(PROJECT_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "agents":
                    offenders.append(str(f.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"app/ must never import agents/: {offenders}"


def test_proposal_repo_unmodified_after_agent_runs(fake_repo, state_dir):
    import subprocess
    from agents import docs_controller, proposal_evaluator, repository_guardian, project_supervisor

    before = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                             capture_output=True, text=True).stdout

    docs_controller.run(fake_repo, state_dir)
    repository_guardian.run(fake_repo, state_dir)
    proposal_evaluator.run(fake_repo, state_dir)
    project_supervisor.run(fake_repo, state_dir)

    after = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                            capture_output=True, text=True).stdout
    assert before == after == ""


def test_state_only_written_to_state_dir(fake_repo, state_dir, tmp_path):
    from agents import docs_controller, proposal_evaluator, repository_guardian, project_supervisor

    other_dir_snapshot = sorted(p.name for p in tmp_path.iterdir())

    docs_controller.run(fake_repo, state_dir)
    repository_guardian.run(fake_repo, state_dir)
    proposal_evaluator.run(fake_repo, state_dir)
    project_supervisor.run(fake_repo, state_dir)

    after_snapshot = sorted(p.name for p in tmp_path.iterdir())
    assert set(after_snapshot) - set(other_dir_snapshot) == set()

    for f in ["docs_state.json", "guardian_state.json", "evaluation_state.json", "supervisor_state.json"]:
        assert (state_dir / f).exists()
