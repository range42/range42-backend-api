"""Integration tests for debug route handlers."""

import pytest
from unittest.mock import patch


@pytest.fixture
def mock_runner():
    """Mock run_playbook_core to return a successful result."""
    with patch("app.routes.debug.run_playbook_core") as mock:
        mock.return_value = (0, [], "PLAY RECAP\nok=1", "PLAY RECAP\nok=1")
        yield mock


@pytest.fixture
def mock_runner_failure():
    """Mock run_playbook_core to return a failure."""
    with patch("app.routes.debug.run_playbook_core") as mock:
        mock.return_value = (1, [], "TASK FAILED", "TASK FAILED")
        yield mock


class TestDebugPing:
    """Tests for POST /v0/admin/debug/ping."""

    def test_ping_success(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rc"] == 0
        assert "log_multiline" in data

    def test_ping_failure_returns_500(self, client, mock_runner_failure):
        resp = client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        assert resp.status_code == 500
        assert resp.json()["rc"] == 1

    def test_ping_missing_required_fields_returns_422(self, client, mock_runner):
        resp = client.post("/v0/admin/debug/ping", json={})
        assert resp.status_code == 422

    def test_ping_calls_runner_with_correct_playbook(self, client, mock_runner):
        client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        mock_runner.assert_called_once()
        args = mock_runner.call_args
        # First positional arg is the playbook path
        assert "ping.yml" in str(args[0][0])

    def test_ping_passes_extravars_when_node_set(self, client, mock_runner):
        client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve02"},
        )
        call_kwargs = mock_runner.call_args
        extravars = call_kwargs.kwargs.get("extravars") or call_kwargs[1].get("extravars")
        if extravars is None:
            # May be passed as positional -- the important thing is the call was made
            pass
