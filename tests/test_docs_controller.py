import json
from pathlib import Path

from agents import docs_controller


def test_docs_controller_reads_only_source_repo(fake_repo, state_dir):
    before = json.loads((fake_repo / "config" / "canonical_documents.json").read_text())
    result = docs_controller.run(fake_repo, state_dir)
    after = json.loads((fake_repo / "config" / "canonical_documents.json").read_text())
    assert before == after
    assert result["status"] == "completed"


def test_docs_controller_writes_only_state_dir(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)
    assert (state_dir / "docs_state.json").exists()
    # nothing new written anywhere inside the fake repo
    import subprocess
    out = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                          capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_docs_controller_detects_frozen_document(fake_repo, state_dir):
    result = docs_controller.run(fake_repo, state_dir)
    doc = result["documents"][0]
    assert doc["path"] == "00_baseline/A.md"
    assert doc["status"] == "FROZEN"
    assert result["schema_version"]
    assert result["repo_commit"]


def test_docs_controller_missing_manifest_fails_cleanly(tmp_path, state_dir):
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    result = docs_controller.run(empty_repo, state_dir)
    assert result["status"] == "failed"
