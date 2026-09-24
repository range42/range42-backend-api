import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from tests.core.test_runtime_firewall import data, preview
from tests.fixtures.runtime_tls_api import tls_api


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["rename", "delete"])
@pytest.mark.parametrize("drift", [None, "before_guard", "after_guard"])
async def test_actual_alias_request_honours_scoped_review_tls_and_native_digest(tmp_path, action, drift):
    from app.core import runtime_networks
    from app.core.runtime_firewall import firewall_change_play
    from app.core.runtime_networks import lifecycle_guard_play
    from app.core.runtime_runner import _ownership_guard
    rows = data()
    request = {"kind": "firewall_alias", "scope": "vm", "vm_id": 3191, "action": action,
               "name": "labclients", "acknowledge_shared_scope": True}
    if action == "rename":
        request["new_name"] = "students"
    plan = await preview(tmp_path, rows, request)
    if drift == "before_guard":
        rows["/nodes/pve01/qemu/3191/firewall/aliases"][0]["cidr"] = "10.99.0.0/24"
    mutations = []
    def respond(method, path, body):
        parsed = urlsplit(path)
        endpoint = parsed.path.removeprefix("/api2/json")
        if method == "GET":
            return (200, rows[endpoint]) if endpoint in rows else (403, None)
        assert endpoint == "/nodes/pve01/qemu/3191/firewall/aliases/labclients"
        assert method == ("PUT" if action == "rename" else "DELETE")
        sent_digest = body.get("digest") if body else parse_qs(parsed.query)["digest"][0]
        if drift == "after_guard":
            return 409, None
        assert sent_digest == "a" * 40
        if action == "rename":
            assert body == {"rename": "students", "cidr": "10.42.70.0/24", "comment": "range42-deployment:dep", "digest": "a" * 40}
        mutations.append(endpoint)
        return 200, None
    inventory = tmp_path / "hosts.yml"
    inventory.write_text("proxmox:\n  hosts:\n    local:\n      ansible_connection: local\n      ansible_python_interpreter: '{{ ansible_playbook_python }}'\n")
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([
        _ownership_guard([{"vm_id": 3191, "vm_name": "owned-guest"}], "dep"), lifecycle_guard_play(plan), firewall_change_play(plan),
    ], sort_keys=False))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled=False\n")
    with tls_api(tmp_path, respond) as (address, ca):
        variables = tmp_path / "vars.json"
        variables.write_text(json.dumps({"proxmox_api_host": address, "proxmox_api_user": "test@pve", "proxmox_api_token_id": "unit",
                                        "proxmox_api_token_secret": "fake", "proxmox_node": "pve01", "r42_deployment_id": "dep"}))
        result = subprocess.run([str(Path(sys.executable).parent / "ansible-playbook"), "-i", str(inventory), str(playbook), "-e", f"@{variables}"],
                                env={**os.environ, "ANSIBLE_CONFIG": str(config), "RANGE42_PROXMOX_CA_FILE": str(ca),
                                     "ANSIBLE_LIBRARY": str(Path(runtime_networks.__file__).parent / "ansible_modules")},
                                capture_output=True, text=True, timeout=45)
    assert (result.returncode == 0) is (drift is None), result.stdout + result.stderr
    assert len(mutations) == int(drift is None)
