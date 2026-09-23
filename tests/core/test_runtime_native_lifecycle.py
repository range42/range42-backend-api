"""Execute the native bundle sequence; replace only the external role boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from tests.core.test_runtime_networks import lifecycle_data, read


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "delete"])
async def test_native_sequence_keeps_reviewed_targets_despite_vault_overrides(tmp_path, monkeypatch, action):
    from app.core import runtime_networks, runtime_runner, native_sdn
    root = os.getenv("RANGE42_NATIVE_CONTRACT_FIXTURE")
    if not root:
        pytest.skip("requires pinned native bundles")
    data = lifecycle_data()
    if action == "create":
        data["/cluster/sdn/vnets"] = []
    observation = await read(tmp_path, data)
    plan = runtime_networks.network_plan({"kind": "sdn_network", "action": action, "vnet": "r42blue"}, observation)
    plan["contract"] = "native-sdn-20260921"
    plan["all_subnets"].append("10.99.0.0/24")
    async def verified(*args):
        return plan
    monkeypatch.setattr(runtime_runner, "_verified_plan", verified)
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(Path(root) / "playbooks/bundles"))
    # Real TLS drift guards and SSH binding have separate execution tests.
    empty = {"hosts": "proxmox", "gather_facts": False, "tasks": []}
    monkeypatch.setattr(runtime_runner, "_ssh_node_guard", lambda: empty)
    monkeypatch.setattr(runtime_networks, "lifecycle_guard_play", lambda _: empty)
    monkeypatch.setattr(native_sdn, "snat_guard_play", lambda: {**empty, "tasks": [
        {"ansible.builtin.set_fact": {"r42_native_nat_before": {"stdout_lines": ["-P POSTROUTING ACCEPT"]}}},
    ]})
    workspace = tmp_path / "workspace"
    secrets = workspace / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "default_vault.yml").write_text(yaml.safe_dump({
        "BUNDLE_SDN_VNET": "foreign", "BUNDLE_SDN_ZONE": "foreign", "BUNDLE_SDN_SUBNET_CIDR": "10.250.0.0/24",
        "BUNDLE_SDN_SUBNET_ID": "foreign", "BUNDLE_SDN_SNAT_WANT": 99, "sdn_vnet_alias": "foreign",
        "sdn_vnet_tag": 123,
    }))
    dep = SimpleNamespace(id="dep", workspace_path=str(workspace), scenario_label="lab")
    attempt = SimpleNamespace(operation={"request": {"kind": "sdn_network"}})
    run = await runtime_runner.prepare_runtime_run(dep, attempt, None, SimpleNamespace(playbook=tmp_path / "main.yml"), tmp_path)
    role = tmp_path / "roles/range42-ansible_roles-proxmox_controller/tasks"
    role.mkdir(parents=True)
    record = tmp_path / "calls.jsonl"
    fields = {key: "{{ " + key + " | default('') }}" for key in (
        "proxmox_vm_action", "sdn_zone", "sdn_vnet", "sdn_vnet_alias", "sdn_vnet_tag", "sdn_subnet", "sdn_subnet_id", "sdn_subnet_cidr", "sdn_snat_want")}
    (role / "main.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.lineinfile": {"path": str(record), "create": True, "mode": "0600", "line": "{{ r42_test_call | to_json }}"}, "vars": {"r42_test_call": fields}},
        {"ansible.builtin.set_fact": {"network_list_snat_rules": []}, "when": "proxmox_vm_action == 'network_list_snat_rules'"},
    ]))
    variables = tmp_path / "extra.json"
    variables.write_text(json.dumps({"proxmox_node": "pve01", **getattr(run, "variables", {})}))
    result = subprocess.run([str(Path(sys.executable).parent / "ansible-playbook"), "-i", str(run.inventory), str(run.playbook), "-e", "@" + str(variables)],
        capture_output=True, text=True, timeout=40, env={**os.environ, "ANSIBLE_ROLES_PATH": str(tmp_path / "roles"), "RANGE42_ACTIVE_CONFIG_DIR": str(run.config_dir)})
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    changes = [row for row in calls if row["proxmox_vm_action"] != "network_list_snat_rules"]
    assert [row["proxmox_vm_action"] for row in changes] == (
        ["network_add_sdn_vnet", "network_add_sdn_subnet"] if action == "create" else ["network_delete_sdn_subnet", "network_delete_sdn_vnet"]
    ) + ["network_apply_sdn", "network_delete_extra_snat_rules", "network_delete_extra_snat_rules"]
    assert changes[0]["sdn_vnet"] == "r42blue"
    assert changes[1]["sdn_vnet"] == "r42blue"
    if action == "create":
        assert changes[0]["sdn_vnet_alias"] == "range42-deployment-dep"
        assert changes[0]["sdn_vnet_tag"] == ""
        assert changes[1]["sdn_subnet"] == "10.42.70.0/24"
    else:
        assert changes[0]["sdn_subnet_id"] == "r42blue-10.42.70.0-24"
    assert changes[-2]["sdn_subnet_cidr"] == "10.42.70.0/24"
    assert changes[-2]["sdn_snat_want"] == (1 if action == "create" else 0)
    assert changes[-1]["sdn_subnet_cidr"] == "10.99.0.0/24"
    assert changes[-1]["sdn_snat_want"] == 0
