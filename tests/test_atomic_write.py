"""
Phase 2A hardening test: atomic_write_json must never leave a partially
written target file, and its temp file must live in the same directory
as the target (same filesystem, so os.replace is atomic).
"""
import json
from pathlib import Path
from unittest import mock

import pytest

from agents.common import atomic_write_json


def test_atomic_write_produces_valid_target(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}


def test_atomic_write_leaves_no_tmp_file_behind(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1})
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.json"]
    assert leftovers == []


def test_atomic_write_failure_does_not_corrupt_existing_target(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"version": 1})

    with mock.patch("agents.common.json.dump", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            atomic_write_json(target, {"version": 2})

    # Original content must be untouched -- os.replace only happens
    # after the temp file is fully written.
    assert json.loads(target.read_text()) == {"version": 1}
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.json"]
    assert leftovers == [], "a failed write must not leave a stray temp file"


def test_atomic_write_tmp_file_same_directory_as_target(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "out.json"
    seen_dirs = []
    import tempfile as tempfile_module
    real_mkstemp = tempfile_module.mkstemp

    def spy_mkstemp(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("agents.common.tempfile.mkstemp", spy_mkstemp)
    atomic_write_json(target, {"a": 1})
    assert seen_dirs == [str(target.parent)]
