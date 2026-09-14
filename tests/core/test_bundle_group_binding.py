"""A static group selector may be narrowed to one verified inventory hostname."""
import json

import pytest
import yaml

from app.core.bundle_attachments import resolve_bundle, validate_scenario_bundles
from app.core.errors import Range42Error
from tests.core.test_bundle_attachments import bundle as bundle, prepare, scenario, write


def group_bundle(bundle, plays=None):
    plays = plays or [{"hosts": "{{ target_group }}", "roles": ["demo"]}]
    descriptor = {"params": [{"name": "target_group", "type": "string", "target": True, "required": True},
                             {"name": "COUNT", "type": "int", "required": True}]}
    for root in (bundle["source"], bundle["runtime"]):
        write(root, bundle["path"] + "/main.yml", yaml.safe_dump(plays))
        write(root, bundle["path"] + "/bundle_parameters.json", json.dumps(descriptor))
    prepare(bundle)
    return resolve_bundle(bundle["source"], source_id="source1", sha="a" * 40, path=bundle["path"])


def group_scenario(tmp_path, result, host="guest-one"):
    root = scenario(tmp_path, result)
    plays = yaml.safe_load((root / "configure.yml").read_text())
    plays[0]["vars"] = {"COUNT": 2, "target_group": host}
    (root / "configure.yml").write_text(yaml.safe_dump(plays))
    return root


def test_group_source_retains_classification_and_seals_single_vm_scope(bundle, tmp_path):
    result = group_bundle(bundle)
    assert result["bundle_kind"] == "GROUP"
    assert result["target_kind"] == "VM"
    assert result["target_vars"] == ["target_group"]
    validate_scenario_bundles(group_scenario(tmp_path, result))


@pytest.mark.parametrize("hosts", ["all", "localhost", "{{ target_group | default('all') }}", "{{ target_ansible_hosts }}", "{{ TARGET_GROUP }}", ["{{ target_group }}"]])
def test_only_the_exact_static_group_selector_can_be_bound(bundle, hosts):
    with pytest.raises(Range42Error):
        group_bundle(bundle, [{"hosts": hosts, "tasks": []}])


@pytest.mark.parametrize("change", [
    {"delegate_to": "localhost"}, {"delegate_to": "{{ other }}"}, {"connection": "local"},
    {"tasks": [{"local_action": "command true"}]},
    {"tasks": [{"ansible.builtin.add_host": {"name": "other", "groups": "guest-one"}}]},
    {"tasks": [{"ansible.builtin.group_by": {"key": "guest-one"}}]},
    {"vars": {"target_group": "all"}}, {"tasks": [{"set_fact": {"target_group": "all"}}]},
    {"vars": {"ansible_host": "10.42.1.99"}}, {"vars": {"ansible_connection": "local"}},
])
def test_group_binding_rejects_scope_changes_in_bundle_plays(bundle, change):
    with pytest.raises(Range42Error):
        group_bundle(bundle, [{"hosts": "{{ target_group }}", **change}])


def test_group_descriptor_cannot_expose_a_connection_override(bundle):
    group_bundle(bundle)
    for root in (bundle["runtime"], bundle["source"]):
        path = root / bundle["path"] / "bundle_parameters.json"
        data = json.loads(path.read_text())
        data["params"].append({"name": "ansible_host", "type": "string"})
        path.write_text(json.dumps(data))
    prepare(bundle)
    with pytest.raises(Range42Error, match="connection"):
        resolve_bundle(bundle["source"], source_id="source1", sha="a" * 40, path=bundle["path"])


@pytest.mark.parametrize("name", ["ansible_host", "ansible_connection", "ansible_user", "ansible_port"])
def test_caller_connection_overrides_are_managed_for_vm_and_group_bundles(name):
    from app.core.bundle_attachments import _parameters
    resolution = {"params": [{"name": name, "type": "string"}], "target_vars": []}
    with pytest.raises(Range42Error, match="managed"):
        _parameters(resolution, {name: "other"})


@pytest.mark.parametrize("extra", [{"hosts": "{{ global_vm_ssh_name }}", "tasks": []}, {"import_playbook": "elsewhere.yml"}, {"tasks": []}])
def test_group_binding_requires_every_play_to_use_the_same_selector(bundle, extra):
    with pytest.raises(Range42Error):
        group_bundle(bundle, [{"hosts": "{{ target_group }}", "tasks": []}, extra])


@pytest.mark.parametrize("host", ["all", "ungrouped", "localhost", "proxmox", "proxmox_cli", "scenario_guests", "guest*", "guest-one,guest-two"])
def test_group_binding_rejects_reserved_or_pattern_inventory_hostnames(bundle, tmp_path, host):
    root = group_scenario(tmp_path, group_bundle(bundle), host)
    (root / "hosts.yml").write_text(yaml.safe_dump({"all": {"hosts": {host: {"ansible_host": "10.42.1.2"}}}}))
    document = json.loads((root / "manifest/scenario_vms.json").read_text())
    document["vms"][0]["vm_name"] = host
    (root / "manifest/scenario_vms.json").write_text(json.dumps(document))
    document = json.loads((root / "manifest/scenario_bundles.json").read_text())
    document["attachments"][0]["inventory_host"] = host
    (root / "manifest/scenario_bundles.json").write_text(json.dumps(document))
    with pytest.raises(Range42Error):
        validate_scenario_bundles(root)


def test_group_binding_rejects_inventory_group_and_host_name_collision(bundle, tmp_path):
    root = group_scenario(tmp_path, group_bundle(bundle))
    inventory = yaml.safe_load((root / "hosts.yml").read_text())
    inventory["all"]["children"]["guest-one"] = {"hosts": {"guest-two": {"ansible_host": "10.42.1.3"}}}
    (root / "hosts.yml").write_text(yaml.safe_dump(inventory))
    with pytest.raises(Range42Error, match="group"):
        validate_scenario_bundles(root)


@pytest.mark.parametrize("change", ["parameter", "callsite", "proof"])
def test_group_binding_cannot_be_widened_by_parameters_or_callsite(bundle, tmp_path, change):
    result = group_bundle(bundle)
    root = group_scenario(tmp_path, result, "all" if change == "callsite" else "guest-one")
    document = json.loads((root / "manifest/scenario_bundles.json").read_text())
    if change == "parameter":
        document["attachments"][0]["parameters"]["target_group"] = "all"
    if change == "proof":
        document["attachments"][0]["resolution"]["target_kind"] = "GROUP"
    (root / "manifest/scenario_bundles.json").write_text(json.dumps(document))
    with pytest.raises(Range42Error):
        validate_scenario_bundles(root)
