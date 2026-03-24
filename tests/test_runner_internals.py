"""Tests for app.core.runner internal helpers."""

import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from app.core.runner import _build_envvars, _build_cmdline, _setup_temp_dir
from app.core.vault import VaultManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestBuildEnvvars:
    """Tests for _build_envvars()."""

    def test_returns_dict_with_ansible_keys(self):
        vm = VaultManager()
        result = _build_envvars(vm)
        assert isinstance(result, dict)
        assert "ANSIBLE_HOST_KEY_CHECKING" in result
        assert "ANSIBLE_DEPRECATION_WARNINGS" in result
        assert "ANSIBLE_COLLECTIONS_PATH" in result

    def test_includes_vault_password_file_from_env(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"VAULT_PASSWORD_FILE": "/tmp/vault-pass.txt"}):
            result = _build_envvars(vm)
            assert result["ANSIBLE_VAULT_PASSWORD_FILE"] == "/tmp/vault-pass.txt"

    def test_includes_vault_path_from_manager(self):
        vm = VaultManager()
        vm.set_vault_path(Path("/tmp/test-vault-path"))
        # Clear env so manager path is used as fallback
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_envvars(vm)
            assert result["ANSIBLE_VAULT_PASSWORD_FILE"] == "/tmp/test-vault-path"
        vm.set_vault_path(None)

    def test_no_vault_key_when_no_vault_configured(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_envvars(vm)
            assert "ANSIBLE_VAULT_PASSWORD_FILE" not in result


class TestBuildCmdline:
    """Tests for _build_cmdline()."""

    def test_returns_none_when_no_vault_no_tags(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert result is None

    def test_appends_vault_password_file(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"VAULT_PASSWORD_FILE": "/tmp/vp.txt"}, clear=False):
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert "--vault-password-file" in result
            assert "/tmp/vp.txt" in result

    def test_appends_tags(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, "install,configure")
            assert "--tags install,configure" in result

    def test_appends_vault_file_as_extra_vars(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"API_BACKEND_VAULT_FILE": "/tmp/vault.yml"}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert '-e "@/tmp/vault.yml"' in result

    def test_preserves_existing_cmdline(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, "--check", None)
            assert result == "--check"


class TestSetupTempDir:
    """Tests for _setup_temp_dir()."""

    def test_creates_temp_directory_structure(self):
        vm = VaultManager()
        playbook = PROJECT_ROOT / "playbooks" / "ping.yml"
        inventory = PROJECT_ROOT / "inventory" / "hosts.yml"
        if not playbook.exists() or not inventory.exists():
            pytest.skip("Missing playbook or inventory fixtures")

        tmp_dir, inv_dest, play_rel = _setup_temp_dir(inventory, playbook, vm)
        try:
            assert tmp_dir.exists()
            assert (tmp_dir / "project").is_dir()
            assert (tmp_dir / "inventory").is_dir()
            assert (tmp_dir / "env" / "envvars").is_file()
            assert inv_dest.exists()
            assert (tmp_dir / "project" / play_rel).exists()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_envvars_file_contains_ansible_keys(self):
        vm = VaultManager()
        playbook = PROJECT_ROOT / "playbooks" / "ping.yml"
        inventory = PROJECT_ROOT / "inventory" / "hosts.yml"
        if not playbook.exists() or not inventory.exists():
            pytest.skip("Missing playbook or inventory fixtures")

        tmp_dir, _, _ = _setup_temp_dir(inventory, playbook, vm)
        try:
            envvars_content = (tmp_dir / "env" / "envvars").read_text()
            assert "ANSIBLE_HOST_KEY_CHECKING" in envvars_content
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
