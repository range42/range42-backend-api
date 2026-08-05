"""Integration tests for the generic bundle/scenario runner routes.

Every bundle on range42-playbooks dev is at least two segments deep
(`<tier>/<subject>.<verb>.<object>`), so the runner must accept slashes in
the name — a single-segment path param 404s on every real bundle.
"""

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_bundle_run():
    """Resolve any bundle name to a dummy path and stub the Ansible run."""
    with (
        patch("app.utils.resolve_bundles_playbook") as resolve,
        patch("app.utils.resolve_inventory") as inventory,
        patch("app.routes.runner.run_playbook_core") as run,
    ):
        resolve.return_value = Path("/tmp/bundle/main.yml")
        inventory.return_value = Path("/tmp/hosts")
        run.return_value = (0, [], "PLAY RECAP\nok=1", "PLAY RECAP\nok=1")
        yield resolve, run


BODY = {"hosts": "all", "proxmox_node": "pve01"}


class TestRunBundle:
    """Tests for POST /v0/admin/run/bundles/{bundles_name:path}/run."""

    @pytest.mark.parametrize(
        "name",
        [
            "generic/systems.baseline.docker_host",
            "generic/systems.configure.add_user",
            "admin/software.install.wazuh",
            "ctf/cve/web/some_challenge",
            "ping",
        ],
    )
    def test_accepts_nested_bundle_names(self, client, mock_bundle_run, name):
        resolve, _ = mock_bundle_run
        resp = client.post(f"/v0/admin/run/bundles/{name}/run", json=BODY)
        assert resp.status_code == 200, f"{name} did not route"
        assert resolve.call_args[0][0] == name

    def test_accepts_nested_scenario_names(self, client, mock_bundle_run):
        with patch("app.utils.resolve_scenarios_playbook") as resolve:
            resolve.return_value = Path("/tmp/scenario/main.yml")
            resp = client.post(
                "/v0/admin/run/scenarios/blank_scenario_2_subnets/run", json=BODY
            )
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "name",
        [
            "..%2f..%2fetc%2fpasswd",
            "generic//x",
            "generic/systems bad",
            "/etc/passwd",
        ],
    )
    def test_rejects_malformed_names(self, client, name):
        """The :path converter must not weaken name validation.

        Bare `..`/`.` segments are normalised away by the HTTP client before
        routing, so they never reach the app; the resolver rejects them
        directly in test_checks_playbooks.py.
        """
        resp = client.post(f"/v0/admin/run/bundles/{name}/run", json=BODY)
        assert resp.status_code == 400
