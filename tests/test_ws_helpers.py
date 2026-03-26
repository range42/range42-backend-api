"""Tests for WebSocket helper functions in app.routes.ws_status."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from app.routes.ws_status import compute_diff, load_proxmox_credentials


class TestComputeDiff:
    """Tests for compute_diff()."""

    def test_no_changes_returns_none(self):
        state = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        assert compute_diff(state, state) is None

    def test_detects_status_change(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        curr = {100: {"vmid": 100, "status": "stopped", "cpu": 0.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert 100 in diff
        assert diff[100]["type"] == "changed"
        assert diff[100]["status"] == "stopped"

    def test_detects_cpu_change_above_threshold(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 10.0}}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 15.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "changed"

    def test_ignores_small_cpu_change(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 10.0}}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 11.0}}
        assert compute_diff(prev, curr) is None

    def test_detects_added_vm(self):
        prev = {}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "added"

    def test_detects_removed_vm(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        curr = {}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "removed"

    def test_empty_states_returns_none(self):
        assert compute_diff({}, {}) is None

    def test_multiple_changes(self):
        prev = {
            100: {"vmid": 100, "status": "running", "cpu": 5.0},
            101: {"vmid": 101, "status": "stopped", "cpu": 0.0},
        }
        curr = {
            100: {"vmid": 100, "status": "stopped", "cpu": 0.0},
            102: {"vmid": 102, "status": "running", "cpu": 10.0},
        }
        diff = compute_diff(prev, curr)
        assert 100 in diff  # changed
        assert 101 in diff  # removed
        assert 102 in diff  # added

    def test_tag_changes_not_detected_by_diff(self):
        """Tags are only synced via full refreshes, not diffs.
        compute_diff only compares status and cpu threshold."""
        prev = {100: {"vmid": 100, "status": "running", "cpu": 5.0, "tags": "admin"}}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 5.0, "tags": "admin;monitoring"}}
        # Tags change alone does NOT trigger a diff
        assert compute_diff(prev, curr) is None

    def test_full_state_includes_tags(self):
        """Verify that VM status dicts include the tags field."""
        vm = {"vmid": 100, "status": "running", "cpu": 5.0, "tags": "admin;vuln"}
        assert "tags" in vm
        assert vm["tags"] == "admin;vuln"


class TestLoadProxmoxCredentials:
    """Tests for load_proxmox_credentials()."""

    def test_returns_empty_dict_on_missing_inventory(self):
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": "/tmp/nonexistent-xyz"}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_returns_empty_dict_on_bad_yaml(self, tmp_path):
        bad_yaml = tmp_path / "hosts.yml"
        bad_yaml.write_text(": invalid: yaml: [[[")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_returns_empty_dict_when_no_proxmox_hosts(self, tmp_path):
        inv = tmp_path / "hosts.yml"
        inv.write_text("all:\n  children:\n    other_group:\n      hosts: {}\n")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_extracts_credentials_from_valid_inventory(self, tmp_path):
        inv = tmp_path / "hosts.yml"
        inv.write_text("""
all:
  children:
    range42_infrastructure:
      children:
        proxmox:
          hosts:
            pve01:
              proxmox_api_host: "192.168.1.100:8006"
              proxmox_node: "pve01"
              proxmox_api_user: "root@pam"
              proxmox_api_token_id: "mytoken"
              proxmox_api_token_secret: "secret123"
""")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result["api_host"] == "192.168.1.100:8006"
            assert result["node"] == "pve01"
            assert "mytoken" in result["token_id"]
            assert result["token_secret"] == "secret123"
