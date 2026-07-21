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


def test_guardian_still_fails_on_genuine_broken_reference(fake_repo, state_dir):
    """Regression guard: none of the new false-positive rulings should
    ever rescue a reference that is actually broken."""
    broken = fake_repo / "00_baseline" / "GENUINE.md"
    broken.write_text("See `NOWHERE_TO_BE_FOUND.md` for details.\n")
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] == "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert any("NOWHERE_TO_BE_FOUND.md" in f["description"] for f in crit)


def test_guardian_finds_target_in_03_implementation(fake_repo, state_dir):
    """03_implementation was missing from SCAN_SUBDIRS -- a genuinely
    existing file there must resolve, not be flagged broken."""
    (fake_repo / "03_implementation").mkdir()
    (fake_repo / "03_implementation" / "PM_ALLOCATION.md").write_text("PM allocation.\n")
    (fake_repo / "00_baseline" / "REF.md").write_text("See `PM_ALLOCATION.md` for details.\n")
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] == "PASS"
    assert result["findings"] == []


def test_guardian_generic_wp_placeholder_is_info_not_critical(fake_repo, state_dir):
    (fake_repo / "00_baseline" / "TEMPLATE.md").write_text(
        "Every partner-facing `WPx_TASKS.md` derived extract (e.g. real files).\n"
    )
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] != "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert crit == []
    info = [f for f in result["findings"] if f["severity"] == "info"]
    assert any(f["canonical_issue_key"] == "WPx_TASKS.md" for f in info)
    assert any("placeholder" in f["title"].lower() for f in info)


def test_guardian_external_repository_citation_is_info_not_critical(fake_repo, state_dir):
    (fake_repo / "00_baseline" / "BASELINE.md").write_text(
        "Recorded in `EXTERNAL_EVIDENCE.md` (external repository: "
        "`some-org/some-public-evidence`, commit `abc123`).\n"
    )
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] != "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert crit == []
    info = [f for f in result["findings"] if f["severity"] == "info"]
    assert any(f["canonical_issue_key"] == "EXTERNAL_EVIDENCE.md" for f in info)
    assert any("external repository" in f["title"].lower() for f in info)


def test_guardian_external_context_window_does_not_leak_to_unrelated_reference(fake_repo, state_dir):
    """The 'external repository' phrase must only rescue a reference
    that actually appears near it -- an unrelated broken reference much
    earlier in the same file must still be flagged critical."""
    text = (
        "See `TRULY_BROKEN.md` for details.\n"
        + ("padding " * 200) + "\n"
        + "Recorded in `SOME_EVIDENCE.md` (external repository: `org/evidence`).\n"
    )
    (fake_repo / "00_baseline" / "MIXED.md").write_text(text)
    result = repository_guardian.run(fake_repo, state_dir)
    assert result["guardian_status"] == "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert any(f["canonical_issue_key"] == "TRULY_BROKEN.md" for f in crit)
    info = [f for f in result["findings"] if f["severity"] == "info"]
    assert any(f["canonical_issue_key"] == "SOME_EVIDENCE.md" for f in info)


def test_guardian_sibling_wp_repo_citation_is_info_not_critical(monkeypatch, fake_repo, state_dir, tmp_path):
    from agents import repository_guardian as rg

    wp_root = tmp_path / "siblings"
    (wp_root / "WP2_FORITECH_PQC_PLATFORM" / "docs").mkdir(parents=True)
    (wp_root / "WP2_FORITECH_PQC_PLATFORM" / "docs" / "WP2_FRAMEWORK.md").write_text("framework doc\n")

    monkeypatch.setenv("FORISEC_WP_REPOS_ROOT", str(wp_root))
    rg._SIBLING_WP_INDEX_CACHE = None  # reset the module-level cache for this test

    (fake_repo / "00_baseline" / "TASKS.md").write_text("See `WP2_FRAMEWORK.md` for details.\n")
    result = rg.run(fake_repo, state_dir)

    rg._SIBLING_WP_INDEX_CACHE = None  # don't leak into other tests

    assert result["guardian_status"] != "FAIL"
    crit = [f for f in result["findings"] if f["severity"] == "critical"]
    assert crit == []
    info = [f for f in result["findings"] if f["severity"] == "info"]
    assert any(f["canonical_issue_key"] == "WP2_FRAMEWORK.md" for f in info)
    assert any("sibling" in f["title"].lower() for f in info)


def test_guardian_sibling_repo_lookup_never_fails_when_repo_missing(monkeypatch, fake_repo, state_dir, tmp_path):
    from agents import repository_guardian as rg

    monkeypatch.setenv("FORISEC_WP_REPOS_ROOT", str(tmp_path / "does_not_exist"))
    rg._SIBLING_WP_INDEX_CACHE = None

    (fake_repo / "00_baseline" / "TASKS.md").write_text("See `NOT_ANYWHERE.md` for details.\n")
    result = rg.run(fake_repo, state_dir)

    rg._SIBLING_WP_INDEX_CACHE = None

    assert result["status"] == "completed"
    assert result["guardian_status"] == "FAIL"
