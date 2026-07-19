"""
Phase 2A.1 — path-containment tests for agents.common.safe_repo_path().
Uses tmp_path only; never touches real system files.
"""
import os
from pathlib import Path

import pytest

from agents.common import safe_repo_path, UnsafeRepositoryPathError


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "sub" / "deep").mkdir(parents=True)
    (r / "sub" / "a.md").write_text("content a")
    (r / "sub" / "deep" / "b.md").write_text("content b")
    return r


def test_normal_relative_path_allowed(repo):
    result = safe_repo_path(repo, "sub/a.md")
    assert result == (repo / "sub" / "a.md").resolve()


def test_nested_valid_path_allowed(repo):
    result = safe_repo_path(repo, "sub/deep/b.md")
    assert result == (repo / "sub" / "deep" / "b.md").resolve()


def test_missing_but_valid_path_inside_repo_allowed(repo):
    # safe_repo_path only validates containment, not existence -- a
    # not-yet-existing file inside the repo is a legitimate result.
    result = safe_repo_path(repo, "sub/does_not_exist.md")
    assert result == (repo / "sub" / "does_not_exist.md").resolve()
    assert not result.exists()


def test_absolute_path_rejected(repo):
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "/etc/passwd")


def test_empty_path_rejected(repo):
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "")


def test_single_dotdot_escape_rejected(repo, tmp_path):
    (tmp_path / "outside.md").write_text("secret")
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "../outside.md")


def test_multiple_dotdot_escape_rejected(repo):
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "../../../../etc/passwd")


def test_symlink_inside_repo_pointing_outside_rejected(repo, tmp_path):
    outside_target = tmp_path / "secret_outside.md"
    outside_target.write_text("do not read me")
    link = repo / "sub" / "link.md"
    link.symlink_to(outside_target)
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "sub/link.md")


def test_symlink_inside_repo_pointing_inside_allowed(repo):
    link = repo / "sub" / "internal_link.md"
    link.symlink_to(repo / "sub" / "a.md")
    result = safe_repo_path(repo, "sub/internal_link.md")
    assert result == (repo / "sub" / "a.md").resolve()


def test_no_string_prefix_bypass(repo, tmp_path):
    # A sibling directory whose name happens to start with the same
    # prefix as repo_root must NOT be treated as "inside" -- this is
    # exactly the class of bug that plain string .startswith() checks
    # are vulnerable to (e.g. "/x/repo" vs "/x/repo-evil").
    sibling = Path(str(repo) + "-evil")
    sibling.mkdir()
    (sibling / "leak.md").write_text("leaked")
    # Constructing a candidate that resolves to the sibling requires an
    # escape; a same-prefix directory is never reachable via a relative
    # candidate resolved against repo_root, so this should raise for
    # any candidate that would reach it via `..`.
    with pytest.raises(UnsafeRepositoryPathError):
        safe_repo_path(repo, "../" + sibling.name + "/leak.md")
