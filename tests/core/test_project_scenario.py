import json

import pytest
import yaml

from app.core import project
from app.core.errors import Range42Error


def scenario_tree(root, *, subdir="", vmids=None):
    scenario = root / subdir / "scenarios" / "content"
    (scenario / "manifest").mkdir(parents=True)
    (scenario / "main.yml").write_text("- import_playbook: configure.yml\n")
    (scenario / "configure.yml").write_text("- hosts: guest\n  tasks: []\n")
    (scenario / "hosts.yml").write_text("all:\n  hosts:\n    guest:\n      ansible_connection: local\n")
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps({
        "scenario": "content", "version": 1,
        "vms": [{"vm_id": vmid} for vmid in (vmids or [])],
    }))
    return scenario


def resolve(root, **kwargs):
    resolver = getattr(project, "resolve_project_scenario", None)
    assert callable(resolver), "Pinned project scenarios need a shared file resolver"
    return resolver(root, scenario_label="content", **kwargs)


def test_concrete_scenario_resolves_shared_repository_subdir(tmp_path):
    scenario = scenario_tree(tmp_path, subdir="projects/lab", vmids=[5000, 5001])
    result = resolve(tmp_path, subdir="projects/lab")
    assert result.project_root == tmp_path / "projects/lab"
    assert result.playbook == scenario / "main.yml"
    assert result.inventory == scenario / "hosts.yml"
    assert result.vmids == [5000, 5001]


def test_internal_runtime_scope_resolves_pinned_manifest_without_executing_main(tmp_path):
    scenario = scenario_tree(tmp_path, vmids=[5000])
    result = resolve(tmp_path, scope="runtime")
    assert result.playbook == scenario / "main.yml"
    assert result.vmids == [5000]


@pytest.mark.parametrize("name", ["main.yml", "hosts.yml", "manifest/scenario_vms.json"])
def test_concrete_scenario_requires_its_own_files(tmp_path, name):
    scenario = scenario_tree(tmp_path)
    (scenario / name).unlink()
    with pytest.raises(Range42Error, match=name):
        resolve(tmp_path)


@pytest.mark.parametrize("subdir", ["../outside", "/tmp", "projects/../../outside"])
def test_concrete_scenario_rejects_project_path_traversal(tmp_path, subdir):
    with pytest.raises(Range42Error, match="subdir"):
        resolve(tmp_path, subdir=subdir)


def test_concrete_scenario_rejects_symlink_to_outside_checkout(tmp_path):
    root = tmp_path / "project"
    scenario = scenario_tree(root)
    outside = tmp_path / "outside.yml"
    outside.write_text("- hosts: all\n")
    (scenario / "main.yml").unlink()
    (scenario / "main.yml").symlink_to(outside)
    with pytest.raises(Range42Error, match="outside"):
        resolve(root)


@pytest.mark.parametrize("manifest", [{}, {"vms": "wrong"}, {"vms": [{}]}, {"vms": [{"vm_id": True}]}])
def test_concrete_scenario_rejects_invalid_manifest(tmp_path, manifest):
    scenario = scenario_tree(tmp_path)
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    with pytest.raises(Range42Error, match="manifest"):
        resolve(tmp_path)


def test_concrete_scenario_rejects_invalid_inventory(tmp_path):
    scenario = scenario_tree(tmp_path)
    (scenario / "hosts.yml").write_text("all: [unterminated")
    with pytest.raises(Range42Error, match="hosts.yml"):
        resolve(tmp_path)


def version3_scenario(root):
    scenario = scenario_tree(root, vmids=[3101])
    (scenario / "teardown.yml").write_text("- hosts: r42-proxmox\n  tasks: []\n")
    manifest = {"scenario": "content", "version": 3, "vms": [{
        "vm_id": 3101, "vm_name": "saved-vm", "template_vm_id": 9901,
        "ip": "10.42.7.10", "bridge": "saved1",
        "nics": [{"index": 0, "ip": "10.42.7.10", "bridge": "saved1", "prefix": 24}],
    }]}
    hosts = {"all": {"children": {
        "proxmox": {"hosts": {"r42-proxmox": {"ansible_connection": "local"}}},
        "scenario_guests": {"hosts": {"saved-vm": {"ansible_host": "10.42.7.10", "ansible_user": "alice"}}},
    }}}
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    (scenario / "hosts.yml").write_text(yaml.safe_dump(hosts))
    return scenario, manifest, hosts


@pytest.mark.parametrize("scope", ["full", "configure", "teardown", "runtime"])
def test_literal_scenario_binds_inventory_for_every_operation(tmp_path, scope):
    version3_scenario(tmp_path)
    assert resolve(tmp_path, scope=scope).vmids == [3101]


def test_nonreplicated_scenario_inventory_allows_more_than_64_guests(tmp_path):
    scenario, manifest, hosts = version3_scenario(tmp_path)
    manifest["vms"] = []
    guests = hosts["all"]["children"]["scenario_guests"]["hosts"] = {}
    for index in range(67):
        name = f"saved-vm-{index}"
        address = f"10.42.7.{index + 10}"
        manifest["vms"].append({
            "vm_id": 3101 + index, "vm_name": name, "template_vm_id": 9901,
            "ip": address, "bridge": "saved1",
            "nics": [{"index": 0, "ip": address, "bridge": "saved1", "prefix": 24}],
        })
        guests[name] = {"ansible_host": address}
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    (scenario / "hosts.yml").write_text(yaml.safe_dump(hosts))

    assert resolve(tmp_path).vmids == list(range(3101, 3168))


@pytest.mark.parametrize("scope", ["full", "configure", "teardown", "runtime"])
def test_literal_scenario_rejects_manifest_host_name_drift(tmp_path, scope):
    scenario, manifest, _ = version3_scenario(tmp_path)
    manifest["vms"][0]["vm_name"] = "inconsistent-host"
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    with pytest.raises(Range42Error, match="hosts.yml"):
        resolve(tmp_path, scope=scope)


@pytest.mark.parametrize("change", ["address", "missing_address", "extra_guest", "duplicate_alias"])
def test_literal_scenario_rejects_unbound_inventory_targets(tmp_path, change):
    scenario, _, hosts = version3_scenario(tmp_path)
    children = hosts["all"]["children"]
    guests = children["scenario_guests"]["hosts"]
    if change == "address":
        guests["saved-vm"]["ansible_host"] = "203.0.113.9"
    elif change == "missing_address":
        del guests["saved-vm"]["ansible_host"]
    elif change == "extra_guest":
        guests["unowned-vm"] = {"ansible_host": "203.0.113.9"}
    else:
        children["other"] = {"hosts": {"saved-vm": {"ansible_host": "203.0.113.9"}}}
    (scenario / "hosts.yml").write_text(yaml.safe_dump(hosts))
    with pytest.raises(Range42Error, match="hosts.yml"):
        resolve(tmp_path)
