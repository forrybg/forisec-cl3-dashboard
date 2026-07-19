"""
Additional Phase 2A safety tests for app/config.py, per the hardening
findings from the code review (state-dir mkdir-on-import, missing
git-repo check, non-dict JSON handling).
"""
import os
import sys
from pathlib import Path

import pytest

from app import config as config_module


def _clear_env(monkeypatch):
    monkeypatch.delenv("FORISEC_REPO_ROOT", raising=False)
    monkeypatch.delenv("FORISEC_STATE_DIR", raising=False)


def test_missing_repo_root_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FORISEC_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(config_module.ConfigError):
        config_module.load_config()


def test_missing_state_dir_raises(monkeypatch, fake_repo):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    with pytest.raises(config_module.ConfigError):
        config_module.load_config()


def test_repo_root_must_be_a_git_repo(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(not_a_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(config_module.ConfigError, match="does not look like a Git repository"):
        config_module.load_config()


def test_state_dir_inside_repo_root_rejected(monkeypatch, fake_repo):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(fake_repo / "state"))
    with pytest.raises(config_module.ConfigError, match="may not be inside"):
        config_module.load_config()


def test_state_dir_inside_old_system_root_rejected(monkeypatch, fake_repo):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(config_module._OLD_SYSTEM_ROOT / "server" / "state" / "x"))
    with pytest.raises(config_module.ConfigError, match="old system root"):
        config_module.load_config()


def test_load_config_default_does_not_create_state_dir(monkeypatch, fake_repo, tmp_path):
    _clear_env(monkeypatch)
    state_dir = tmp_path / "brand_new_state_dir"
    assert not state_dir.exists()
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(state_dir))
    config_module.load_config()  # create_state_dir defaults to False
    assert not state_dir.exists(), "load_config() must not create the state dir by default"


def test_importing_app_main_has_no_filesystem_side_effect(monkeypatch, fake_repo, tmp_path):
    state_dir = tmp_path / "not_yet_created"
    monkeypatch.setenv("FORISEC_REPO_ROOT", str(fake_repo))
    monkeypatch.setenv("FORISEC_STATE_DIR", str(state_dir))
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import importlib
    importlib.import_module("app.main")
    assert not state_dir.exists(), "importing app.main must not create the state directory"
