"""Execute the UI's generated firewall adapter with real Ansible and inert PVE boundaries.

This checks the application contract, not live firewall enforcement. The paired
checkout supplies the actual JS generator; only HTTP reads and native composites
are replaced, so the generated guards, source expressions and ordering execute.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


@pytest.mark.parametrize("case", ["default", "armed", "bad_sources", "foreign", "reassigned"])
def test_ui_native_firewall_adapter_with_backend_preferences(tmp_path, monkeypatch, case):
    from app.core import runtime_operations
    from app.core.scenario_preferences import storage_runtime_variables
    helper = Path(__file__).resolve().parents[3] / "range42-deployer-ui/src/services/scenarioFirewall.js"
    if not helper.exists() or not shutil.which("node"):
        if os.getenv("CI"):
            pytest.fail("The paired native-firewall contract requires the UI checkout and Node")
        pytest.skip("The paired native-firewall contract requires the UI checkout and Node")
    vm = {"vm_id": 3101, "vm_name": "test-guest", "ip": "10.42.10.10", "bridge": "lab1",
          "nics": [{"index": 0, "ip": "10.42.10.10", "bridge": "lab1", "prefix": 23}]}
    generated = subprocess.run(["node", "--input-type=module", "-e",
        "const {firewallStages}=await import(process.argv[1]); const {readFileSync}=await import('node:fs');"
        "const input=JSON.parse(readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(firewallStages(input.vms,input.networks)));",
        helper.as_uri()], input=json.dumps({"vms": [vm], "networks": [{"subnet": "10.42.10.0/23"}]}),
        capture_output=True, text=True, check=True)
    plays = json.loads(generated.stdout)
    (tmp_path / "manifest").mkdir()
    (tmp_path / "manifest/scenario_vms.json").write_text(json.dumps({"version": 3, "vms": [vm], "templates": [{"vm_id": 9232}]}))
    (tmp_path / "manifest/scenario_firewall.json").write_text(json.dumps({"version": 1,
        "prepare_management_access": False, "arm_vms": case in ("armed", "reassigned"), "ssh_sources": None,
    }))
    monkeypatch.setattr(runtime_operations, "operation_profile", lambda kind: {"contract": "native-sdn-20260921"})
    variables = storage_runtime_variables(tmp_path)
    variables.update(r42_deployment_id="dep", test_owner="other" if case == "foreign" else "dep")
    if case == "reassigned":
        # Facts cannot override extra vars. This case changes the observed VM,
        # after configure, independently of the deployment's own identity.
        del variables["test_owner"]
    (tmp_path / "extras.yml").write_text(yaml.safe_dump(variables))
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/default_vault.yml").write_text(yaml.safe_dump({
        "FIREWALL_ARM_VMS": "YES",  # the default policy must override this
        "range42_fw_vm_ssh_sources": ["{{ not_a_source }}"] if case == "bad_sources" else ["203.0.113.7/32"],
    }))
    for name, document in plays.items():
        for play in document:
            for task in play.get("tasks", []):
                if "ansible.builtin.uri" in task:
                    del task["ansible.builtin.uri"]
                    del task["register"]
                    task["ansible.builtin.set_fact"] = {"r42_fw_owned_config": {"json": {"data": {
                        "name": vm["vm_name"], "description": "{{ 'range42-deployment:' ~ (test_owner | default('dep')) }}",
                    }}}}
        (tmp_path / name).write_text(yaml.safe_dump(document, sort_keys=False))
    role = tmp_path / "roles/range42-ansible_roles-proxmox_controller/tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.set_fact": {"test_actions": "{{ (test_actions | default([])) + [{'action': proxmox_vm_action, 'vmid': vm_id, 'source': vm_fw_source | default('any')}] }}"}},
        {"ansible.builtin.copy": {"dest": str(tmp_path / "mutations.json"), "content": "{{ test_actions | to_json }}"}},
    ]))
    for bundle in ("firewall.report.status", "firewall.baseline.management_access", "firewall.enable.vms"):
        directory = tmp_path / "bundles/firewall/in_proxmox" / bundle
        directory.mkdir(parents=True)
        (directory / "main.yml").write_text(yaml.safe_dump([{"hosts": "proxmox", "gather_facts": False, "tasks": [
            {"ansible.builtin.set_fact": {"test_stages": "{{ (test_stages | default([])) + ['" + bundle + "'] }}"}},
        ]}]))
    (tmp_path / "main.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.import_playbook": "00_firewall_pre.yml"},
        {"ansible.builtin.import_playbook": "02_firewall_guests.yml"},
        {"hosts": "proxmox", "gather_facts": False, "tasks": [{"ansible.builtin.set_fact": {
            "test_stages": "{{ test_stages + ['configure'] }}", "test_owner": "other" if case == "reassigned" else "dep",
        }}]},
        {"ansible.builtin.import_playbook": "99_firewall_finalize.yml"},
        {"hosts": "proxmox", "gather_facts": False, "tasks": [{"ansible.builtin.copy": {
            "dest": str(tmp_path / "stages.json"), "content": "{{ test_stages | to_json }}",
        }}]},
    ], sort_keys=False))
    (tmp_path / "inventory.yml").write_text("proxmox:\n  hosts:\n    localhost:\n      ansible_connection: local\n")
    (tmp_path / "ansible.cfg").write_text("[defaults]\nretry_files_enabled=False\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ANSIBLE_")}
    env.update(ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"), ANSIBLE_ROLES_PATH=str(tmp_path / "roles"),
               RANGE42_BUNDLE_DIR=str(tmp_path / "bundles"), RANGE42_ACTIVE_CONFIG_DIR=str(tmp_path), ANSIBLE_NOCOLOR="1")
    result = subprocess.run([str(Path(sys.executable).with_name("ansible-playbook")), "-i", "inventory.yml", "-e", "@extras.yml", "main.yml"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    if case in ("bad_sources", "foreign", "reassigned"):
        assert result.returncode == 2, result.stdout + result.stderr
        assert not (tmp_path / "stages.json").exists()
        if case != "reassigned":
            assert not (tmp_path / "mutations.json").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads((tmp_path / "mutations.json").read_text()) == [{
            "action": "firewall_vm_declare_iptables_port", "vmid": 3101, "source": "10.42.10.0/23,203.0.113.7",
        }]
        assert json.loads((tmp_path / "stages.json").read_text()) == [
            "firewall.report.status", "configure", *(["firewall.enable.vms"] if case == "armed" else []), "firewall.report.status",
        ]
