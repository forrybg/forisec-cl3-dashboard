from agents import repository_guardian


def test_guardian_detects_missing_referenced_file(fake_repo, state_dir):
    broken = fake_repo / "00_baseline" / "BROKEN.md"
    broken.write_text("See `00_baseline/DOES_NOT_EXIST.md` for details.\n")
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] == "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert any("DOES_NOT_EXIST.md" in f["description"] for f in crit)


def test_guardian_clean_repo_passes(fake_repo, state_dir):
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] == "PASS"
    assert result["findings"] == []


def test_guardian_bare_filename_reference_resolves(fake_repo, state_dir):
    (fake_repo / "00_baseline" / "REF.md").write_text("See `A.md` for details.\n")
    result = repository_guardian.run(fake_repo, state_dir)
    # `A.md` resolves by basename to 00_baseline/A.md -- not a broken ref
    assert result["guardian_status"] == "PASS"
