import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def fake_repo(tmp_path):
    """A throwaway git repo standing in for the real proposal repo, so
    agent tests never touch /home/forybg/code/.../forisec-cl3-2026."""
    repo = tmp_path / "fake_repo"
    (repo / "config").mkdir(parents=True)
    (repo / "00_baseline").mkdir()
    (repo / "04_proposal").mkdir()

    manifest = {
        "manifest_version": "test",
        "current_phase": "baseline",
        "phases": {"baseline": {"depends_on": []}},
        "documents": [
            {"path": "00_baseline/A.md", "title": "A", "required_phase": "baseline", "required": True},
        ],
    }
    import json
    (repo / "config" / "canonical_documents.json").write_text(json.dumps(manifest))
    (repo / "00_baseline" / "A.md").write_text("<!-- CANONICAL_STATUS: FROZEN -->\ncontent\n")

    subprocess.run(["git", "init", "-q"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=repo)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    return repo


@pytest.fixture
def state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d
