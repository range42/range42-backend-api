"""Integration tests for VM route handlers."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import app.utils


@pytest.fixture(autouse=True)
def _patch_utils_resolve(monkeypatch):
    """Override utils.resolve_inventory; monkeypatch auto-restores the real
    re-exported function after each test."""
    fake = MagicMock(return_value=Path("/tmp/fake/hosts.yml"))
    monkeypatch.setattr(app.utils, "resolve_inventory", fake)
    yield


@pytest.fixture
def mock_runner():
    """Mock run_playbook_core for VM routes."""
    with patch("app.routes.vms.run_playbook_core") as mock, \
         patch.object(Path, "exists", return_value=True):
        mock.return_value = (0, [], "ok", "ok")
        yield mock


@pytest.fixture
def mock_runner_failure():
    with patch("app.routes.vms.run_playbook_core") as mock, \
         patch.object(Path, "exists", return_value=True):
        mock.return_value = (1, [], "FAILED", "FAILED")
        yield mock


class TestVmList:
    """Tests for POST /v0/admin/proxmox/vms/list."""

    def test_list_vms_success(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/proxmox/vms/list",
            json={"proxmox_node": "pve01", "as_json": False},
        )
        assert resp.status_code == 200
        assert "log_multiline" in resp.json()

    def test_list_vms_failure_returns_500(self, client, mock_runner_failure):
        resp = client.post(
            "/v0/admin/proxmox/vms/list",
            json={"proxmox_node": "pve01", "as_json": False},
        )
        assert resp.status_code == 500

    def test_list_vms_missing_required_field_returns_422(self, client, mock_runner):
        resp = client.post("/v0/admin/proxmox/vms/list", json={})
        assert resp.status_code == 422


class TestVmLifecycle:
    """Tests for VM start/stop/pause/resume."""

    @pytest.mark.parametrize("action", ["start", "stop", "stop_force", "pause", "resume"])
    def test_vm_action_success(self, client, mock_runner, action):
        resp = client.post(
            f"/v0/admin/proxmox/vms/vm_id/{action}",
            json={"proxmox_node": "pve01", "vm_id": "100", "as_json": False},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("action", ["start", "stop"])
    def test_vm_action_failure_returns_500(self, client, mock_runner_failure, action):
        resp = client.post(
            f"/v0/admin/proxmox/vms/vm_id/{action}",
            json={"proxmox_node": "pve01", "vm_id": "100", "as_json": False},
        )
        assert resp.status_code == 500


class TestVmListUsageFields:
    """Verify list_usage returns fields consumed by frontend."""

    def test_list_usage_success(self, client, mock_runner):
        mock_runner.return_value = (0, [{
            "event": "runner_on_ok",
            "event_data": {
                "res": {
                    "vm_list_usage": [{
                        "vm_id": 1023,
                        "vm_name": "test-vm",
                        "vm_status": "running",
                        "cpu_current_usage": 35.0,
                        "cpu_allocated": 4,
                        "ram_current_usage": 5264621568,
                        "ram_max": 8589934592,
                        "disk_current_usage": 0,
                        "disk_read": 1258291,
                        "disk_write": 419430,
                        "disk_max": 34359738368,
                        "net_in": 3670016,
                        "net_out": 838860,
                        "vm_uptime": 308520,
                    }]
                }
            }
        }], "ok", "ok")
        resp = client.post(
            "/v0/admin/proxmox/vms/list_usage",
            json={"proxmox_node": "pve01", "as_json": True},
        )
        assert resp.status_code == 200
