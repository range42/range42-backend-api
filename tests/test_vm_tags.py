"""Tests for VM tag operations."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import app.utils


@pytest.fixture(autouse=True)
def _patch_utils_resolve():
    fake = MagicMock(return_value=Path("/tmp/fake/hosts.yml"))
    app.utils.resolve_inventory = fake
    yield
    delattr(app.utils, "resolve_inventory")


@pytest.fixture
def mock_runner():
    with patch("app.routes.vm_config.run_playbook_core") as mock, \
         patch.object(Path, "exists", return_value=True):
        mock.return_value = (0, [], "ok", "ok")
        yield mock


class TestVmSetTag:
    """Tests for POST /v0/admin/proxmox/vms/vm_id/config/vm_set_tag."""

    def test_set_tag_success(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/proxmox/vms/vm_id/config/vm_set_tag",
            json={
                "proxmox_node": "pve01",
                "vm_id": "1023",
                "vm_tag_name": "admin,monitoring",
                "as_json": True,
            },
        )
        assert resp.status_code == 200
        mock_runner.assert_called_once()
        call_kwargs = mock_runner.call_args
        extravars = call_kwargs.kwargs.get("extravars") or call_kwargs[1].get("extravars", {})
        assert extravars.get("proxmox_vm_action") == "vm_set_tag"

    def test_set_tag_empty_string_rejected(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/proxmox/vms/vm_id/config/vm_set_tag",
            json={
                "proxmox_node": "pve01",
                "vm_id": "1023",
                "vm_tag_name": "",
            },
        )
        assert resp.status_code == 422

    def test_set_tag_special_chars_rejected(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/proxmox/vms/vm_id/config/vm_set_tag",
            json={
                "proxmox_node": "pve01",
                "vm_id": "1023",
                "vm_tag_name": "admin;drop table",
            },
        )
        assert resp.status_code == 422


class TestTagFormatConversion:
    """Test format conversion between Proxmox semicolons and backend commas."""

    def test_proxmox_semicolons_parsed(self):
        raw = "admin;monitoring;custom-tag"
        tags = raw.split(";")
        assert tags == ["admin", "monitoring", "custom-tag"]

    def test_backend_comma_format(self):
        tags = ["admin", "monitoring"]
        formatted = ",".join(tags)
        assert formatted == "admin,monitoring"

    def test_empty_tags_handled(self):
        assert "".split(";") == [""]
        assert [t for t in "".split(";") if t] == []
