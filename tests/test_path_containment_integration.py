"""
Phase 2A.1 — integration tests: docs_controller and repository_guardian
must never read outside the repository root, must report a
deterministic HIGH "unsafe-repository-path" finding instead, and must
never leak external file contents into state JSON.
"""
import json

from agents import docs_controller, repository_guardian


def test_docs_controller_rejects_absolute_manifest_path(fake_repo, state_dir, tmp_path):
    secret = tmp_path / "secret_outside.md"
    secret.write_text("TOP SECRET CONTENT, must never appear in state")

    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"].append({
        "path": str(secret), "title": "Escaping doc",
        "required_phase": "baseline", "required": True,
    })
    manifest_path.write_text(json.dumps(manifest))

    result = docs_controller.run(fake_repo, state_dir)

    unsafe_findings = [f for f in result["findings"] if f["id"].startswith("unsafe-repository-path")]
    assert len(unsafe_findings) == 1
    assert unsafe_findings[0]["severity"] == "high"

    # The external file's content must never appear anywhere in state.
    state_text = json.dumps(result)
    assert "TOP SECRET CONTENT" not in state_text


def test_docs_controller_rejects_dotdot_escape_manifest_path(fake_repo, state_dir, tmp_path):
    secret = tmp_path / "escape_target.md"
    secret.write_text("should never be read")

    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"].append({
        "path": "../escape_target.md", "title": "Dotdot doc",
        "required_phase": "baseline", "required": True,
    })
    manifest_path.write_text(json.dumps(manifest))

    result = docs_controller.run(fake_repo, state_dir)
    unsafe_findings = [f for f in result["findings"] if f["id"].startswith("unsafe-repository-path")]
    assert len(unsafe_findings) == 1
    assert "should never be read" not in json.dumps(result)


def test_docs_controller_does_not_read_outside_repo(fake_repo, state_dir, tmp_path, monkeypatch):
    """
    Belt-and-braces: patch Path.read_text globally during the run and
    assert it is never called with a path outside fake_repo.
    """
    from pathlib import Path as PathClass
    original_read_text = PathClass.read_text
    calls = []

    def spy_read_text(self, *args, **kwargs):
        calls.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(PathClass, "read_text", spy_read_text)

    secret = tmp_path / "outside2.md"
    secret.write_text("nope")
    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(original_read_text(manifest_path))
    manifest["documents"].append({
        "path": "../outside2.md", "title": "x",
        "required_phase": "baseline", "required": True,
    })
    manifest_path.write_text(json.dumps(manifest))

    docs_controller.run(fake_repo, state_dir)

    for called_path in calls:
        resolved = called_path.resolve()
        assert str(resolved).startswith(str(fake_repo.resolve())), (
            f"docs_controller read a file outside the repo: {resolved}"
        )


def test_repository_guardian_reports_unsafe_path(fake_repo, state_dir, tmp_path):
    secret = tmp_path / "guardian_secret.md"
    secret.write_text("guardian must never read this")

    bad_ref_file = fake_repo / "00_baseline" / "BAD_REF.md"
    bad_ref_file.write_text("See `../guardian_secret.md` for details.\n")

    result = repository_guardian.run(fake_repo, state_dir)

    unsafe = [f for f in result["findings"] if f["id"].startswith("unsafe-repository-path")]
    assert len(unsafe) == 1
    assert unsafe[0]["severity"] == "high"
    assert "guardian must never read this" not in json.dumps(result)


def test_proposal_repo_unchanged_after_unsafe_path_run(fake_repo, state_dir, tmp_path):
    import subprocess

    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"].append({
        "path": "/etc/passwd", "title": "abs doc",
        "required_phase": "baseline", "required": True,
    })
    manifest_path.write_text(json.dumps(manifest))
    subprocess.run(["git", "add", "-A"], cwd=fake_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add unsafe manifest entry"], cwd=fake_repo, check=True)

    before = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                             capture_output=True, text=True).stdout

    docs_controller.run(fake_repo, state_dir)
    repository_guardian.run(fake_repo, state_dir)

    after = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo,
                            capture_output=True, text=True).stdout
    assert before == after == ""


def test_no_external_file_contents_in_state_json(fake_repo, state_dir, tmp_path):
    marker = "UNIQUE_MARKER_MUST_NOT_LEAK_9f8e7d"
    secret = tmp_path / "leak_test.md"
    secret.write_text(marker)

    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"].append({
        "path": "../leak_test.md", "title": "leak doc",
        "required_phase": "baseline", "required": True,
    })
    manifest_path.write_text(json.dumps(manifest))

    docs_result = docs_controller.run(fake_repo, state_dir)
    guardian_result = repository_guardian.run(fake_repo, state_dir)

    assert marker not in json.dumps(docs_result)
    assert marker not in json.dumps(guardian_result)
    for f in ["docs_state.json", "guardian_state.json"]:
        assert marker not in (state_dir / f).read_text()
