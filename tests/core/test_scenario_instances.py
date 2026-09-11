"""Replication intent must agree with the literal inventory that will execute."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from app.core.errors import Range42Error
from app.core.project import resolve_project_scenario
from app.core.scenario import _configuration_targets
from app.core.scenario_instances import validate_scenario_instances


def key(kind, source, team=None, user=None):
    encoded = json.dumps(["classroom", source, team, user], separators=(",", ":"))
    return kind + "-" + hashlib.sha256(encoded.encode()).hexdigest()


@pytest.fixture
def scenario(tmp_path):
    directory = tmp_path / "scenarios/classroom"
    (directory / "manifest").mkdir(parents=True)
    (directory / "main.yml").write_text("[]\n")
    (directory / "configure.yml").write_text("[]\n")
    network = {"instance_key": key("net", "lan"), "source_node_id": "lan",
               "team_id": None, "user_id": None, "vnet": "classnet",
               "subnet": "10.50.1.0/24", "gateway": "10.50.1.1", "snat": True}
    document = {"version": 1, "scenario_id": "classroom", "intent": {
        "teams": [{"id": "blue", "users": [{"id": "alice"}, {"id": "bob"}]},
                  {"id": "red", "users": [{"id": "alice"}]}],
        "node_scopes": {"desktop": "per_user"}, "network_scopes": {"lan": "shared"}},
        "networks": [network], "instances": []}
    vms = []
    hosts = {}
    for offset, (team, user) in enumerate([("blue", "alice"), ("blue", "bob"), ("red", "alice")]):
        hostname = f"desktop-{offset}"
        ip = f"10.50.1.{offset + 10}"
        document["instances"].append({"instance_key": key("vm", "desktop", team, user),
            "source_node_id": "desktop", "team_id": team, "user_id": user,
            "vm_id": 3200 + offset, "hostname": hostname,
            "nics": [{"index": 0, "nic_key": "desktop-lan", "network_instance_key": network["instance_key"]}]})
        vms.append({"vm_id": 3200 + offset, "vm_name": hostname, "ip": ip, "bridge": "classnet",
                    "nics": [{"index": 0, "ip": ip, "bridge": "classnet", "prefix": 24,
                              "gateway": "10.50.1.1"}]})
        hosts[hostname] = {"ansible_host": ip}
    vm_document = {"version": 3, "vms": vms}
    network_document = {"mode": "sdn", "zone": "class", "vnets": [
        {k: network[k] for k in ("vnet", "subnet", "gateway", "snat")}]}
    inventory = {"all": {"children": {"scenario_guests": {"hosts": hosts},
        "proxmox": {"hosts": {"r42-proxmox": {"ansible_connection": "local"}}},
        "proxmox_cli": {"hosts": {"r42-proxmox-cli": {"ansible_host": "{{ r42_proxmox_address }}"}}}}}}

    def save():
        for filename, value in (("scenario_vms.json", vm_document), ("scenario_networks.json", network_document),
                                ("scenario_instances.json", document)):
            (directory / "manifest" / filename).write_text(json.dumps(value))
        (directory / "hosts.yml").write_text(yaml.safe_dump(inventory))
        return resolve_project_scenario(tmp_path, scenario_label="classroom")

    return document, vm_document, network_document, inventory, save, directory


def test_nested_user_roster_resolves_every_literal_instance(scenario):
    *_, save, directory = scenario
    resolved = save()
    assert resolved.vmids == [3200, 3201, 3202]
    assert resolved.inventory == directory / "hosts.yml"


@pytest.mark.parametrize("mutation", [
    "missing_user", "extra_user", "duplicate_instance", "wrong_key", "wrong_hostname", "wrong_vmid",
    "missing_nic", "unknown_network", "duplicate_nic_key", "different_source_nic", "wrong_bridge",
    "wrong_prefix", "wrong_gateway", "outside_subnet", "gateway_address", "network_shape",
    "missing_network", "wrong_inventory_address", "extra_inventory_host", "duplicate_inventory_host",
    "unknown_scope", "duplicate_team", "duplicate_user", "unknown_field", "bool_version",
])
def test_rejects_inconsistent_replication_before_execution(scenario, mutation):
    doc, vms, networks, inventory, save, _ = scenario
    first = doc["instances"][0]
    if mutation == "missing_user":
        doc["instances"].pop()
    elif mutation == "extra_user":
        doc["intent"]["teams"][0]["users"].pop()
    elif mutation == "duplicate_instance":
        doc["instances"][1] = deepcopy(first)
    elif mutation == "wrong_key":
        first["instance_key"] = key("vm", "desktop", "red", "alice")
    elif mutation == "wrong_hostname":
        first["hostname"] = "another-vm"
    elif mutation == "wrong_vmid":
        first["vm_id"] = 9999
    elif mutation == "missing_nic":
        first["nics"] = []
    elif mutation == "unknown_network":
        first["nics"][0]["network_instance_key"] = key("net", "other")
    elif mutation == "duplicate_nic_key":
        first["nics"].append({**first["nics"][0], "index": 1})
    elif mutation == "different_source_nic":
        first["nics"][0]["nic_key"] = "another-edge"
    elif mutation == "wrong_bridge":
        vms["vms"][0]["bridge"] = vms["vms"][0]["nics"][0]["bridge"] = "foreign"
    elif mutation == "wrong_prefix":
        vms["vms"][0]["nics"][0]["prefix"] = 25
    elif mutation == "wrong_gateway":
        vms["vms"][0]["nics"][0]["gateway"] = "10.50.1.2"
    elif mutation in ("outside_subnet", "gateway_address"):
        address = "10.99.0.1" if mutation == "outside_subnet" else "10.50.1.1"
        vms["vms"][0]["ip"] = vms["vms"][0]["nics"][0]["ip"] = address
        inventory["all"]["children"]["scenario_guests"]["hosts"][first["hostname"]]["ansible_host"] = address
    elif mutation == "network_shape":
        networks["vnets"][0]["snat"] = False
    elif mutation == "missing_network":
        doc["networks"] = []
    elif mutation == "wrong_inventory_address":
        inventory["all"]["children"]["scenario_guests"]["hosts"][first["hostname"]]["ansible_host"] = "10.99.1.4"
    elif mutation == "extra_inventory_host":
        inventory["all"]["hosts"] = {"untracked": {"ansible_host": "10.50.1.50"}}
    elif mutation == "duplicate_inventory_host":
        inventory["all"]["hosts"] = {first["hostname"]: {"ansible_host": "10.50.1.50"}}
    elif mutation == "unknown_scope":
        doc["intent"]["node_scopes"]["desktop"] = "per_person"
    elif mutation == "duplicate_team":
        doc["intent"]["teams"].append(deepcopy(doc["intent"]["teams"][0]))
    elif mutation == "duplicate_user":
        doc["intent"]["teams"][0]["users"].append({"id": "alice"})
    elif mutation == "unknown_field":
        first["token"] = "never-echo-this-value"
    else:
        doc["version"] = True
    with pytest.raises(Range42Error) as exc:
        save()
    assert exc.value.code == "PROJECT_SCENARIO_INVALID"
    assert "never-echo-this-value" not in exc.value.message


def test_configuration_comparison_includes_replication_intent(scenario):
    doc, *_, save, _ = scenario
    original = _configuration_targets(save())
    # Same literal guests but a different source identity must not silently
    # retarget the durable per-instance mapping during a content-only configure.
    doc["intent"]["node_scopes"] = {"replacement": "per_user"}
    for vm in doc["instances"]:
        vm["source_node_id"] = "replacement"
        vm["instance_key"] = key("vm", "replacement", vm["team_id"], vm["user_id"])
    assert _configuration_targets(save()) != original


@pytest.mark.parametrize("kind", ["oversized", "symlink", "malformed"])
def test_invalid_instance_file_is_not_ignored(scenario, kind, tmp_path):
    *_, save, directory = scenario
    save()
    path = directory / "manifest/scenario_instances.json"
    if kind == "oversized":
        path.write_text(" " * 262145)
    elif kind == "malformed":
        path.write_text("{")
    else:
        target = tmp_path / "elsewhere.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(Range42Error):
        resolve_project_scenario(tmp_path, scenario_label="classroom")


def test_existing_concrete_scenario_without_replication_still_resolves(scenario):
    *_, save, directory = scenario
    save()
    (directory / "manifest/scenario_instances.json").unlink()
    assert resolve_project_scenario(directory.parents[1], scenario_label="classroom").vmids == [3200, 3201, 3202]


def scope_networks(scenario, scope):
    doc, vms, networks, inventory, *_ = scenario
    doc["intent"]["network_scopes"]["lan"] = scope
    expanded = {}
    for instance, vm in zip(doc["instances"], vms["vms"], strict=True):
        team = instance["team_id"] if scope != "shared" else None
        user = instance["user_id"] if scope == "per_user" else None
        identity = key("net", "lan", team, user)
        if identity not in expanded:
            index = len(expanded) + 1
            expanded[identity] = {"instance_key": identity, "source_node_id": "lan", "team_id": team,
                "user_id": user, "vnet": f"net{index}", "subnet": f"10.50.{index}.0/24",
                "gateway": f"10.50.{index}.1", "snat": True}
        network = expanded[identity]
        index = list(expanded).index(identity) + 1
        ip = f"10.50.{index}.{vm['vm_id'] - 3190}"
        instance["nics"][0]["network_instance_key"] = identity
        vm.update(ip=ip, bridge=network["vnet"])
        vm["nics"][0].update(ip=ip, bridge=network["vnet"], gateway=network["gateway"])
        inventory["all"]["children"]["scenario_guests"]["hosts"][instance["hostname"]]["ansible_host"] = ip
    doc["networks"] = list(expanded.values())
    networks["vnets"] = [{k: network[k] for k in ("vnet", "subnet", "gateway", "snat")} for network in doc["networks"]]


@pytest.mark.parametrize("scope", ["shared", "per_team", "per_user"])
def test_user_instances_connect_to_their_own_or_shared_network(scenario, scope):
    scope_networks(scenario, scope)
    assert scenario[4]().vmids == [3200, 3201, 3202]


@pytest.mark.parametrize("scope", ["per_team", "per_user"])
def test_coherent_but_cross_cohort_nic_is_rejected(scenario, scope):
    scope_networks(scenario, scope)
    doc, vms, _, inventory, save, _ = scenario
    # Change all concrete values consistently. Only the wrong cohort remains;
    # ordinary bridge/IP/inventory equality is insufficient to reject this.
    foreign = doc["networks"][-1]
    doc["instances"][0]["nics"][0]["network_instance_key"] = foreign["instance_key"]
    ip = foreign["gateway"].rsplit(".", 1)[0] + ".60"
    vms["vms"][0].update(bridge=foreign["vnet"], ip=ip)
    vms["vms"][0]["nics"][0].update(bridge=foreign["vnet"], ip=ip, gateway=foreign["gateway"])
    inventory["all"]["children"]["scenario_guests"]["hosts"][doc["instances"][0]["hostname"]]["ansible_host"] = ip
    with pytest.raises(Range42Error):
        save()


def test_reordering_teams_and_users_preserves_instance_identity(scenario):
    doc, *_, save, _ = scenario
    before = save().vmids
    doc["intent"]["teams"].reverse()
    doc["intent"]["teams"][1]["users"].reverse()
    assert save().vmids == before


@pytest.mark.parametrize("kind", ["empty_roster", "empty_users", "too_many_vms", "too_many_networks", "cyclic_inventory"])
def test_invalid_or_excessive_replication_intent_is_rejected(scenario, kind):
    doc, _, _, inventory, save, _ = scenario
    if kind == "empty_roster":
        doc["intent"]["teams"] = []
    elif kind == "empty_users":
        for team in doc["intent"]["teams"]:
            team["users"] = []
    elif kind == "too_many_vms":
        doc["intent"]["teams"] = [{"id": "large", "users": [{"id": f"user{i}"} for i in range(64)]}]
        doc["intent"]["node_scopes"]["second"] = "per_user"
    elif kind == "too_many_networks":
        doc["intent"]["teams"] = [{"id": f"team{i}", "users": []} for i in range(33)]
        doc["intent"]["node_scopes"]["desktop"] = "shared"
        doc["intent"]["network_scopes"]["lan"] = "per_team"
    else:
        inventory["all"]["children"]["loop"] = inventory["all"]
    with pytest.raises(Range42Error):
        save()


def test_parallel_nics_have_distinct_keys_and_only_one_gateway(scenario):
    doc, vms, _, _, save, _ = scenario
    for instance, vm in zip(doc["instances"], vms["vms"], strict=True):
        instance["nics"].append({**instance["nics"][0], "index": 1, "nic_key": "secondary-lan"})
        vm["nics"].append({"index": 1, "ip": f"10.50.1.{vm['vm_id'] - 3100}", "bridge": "classnet", "prefix": 24})
    assert save().vmids == [3200, 3201, 3202]
    vms["vms"][0]["nics"][1]["gateway"] = "10.50.1.1"
    with pytest.raises(Range42Error):
        save()


@pytest.mark.parametrize("duplicate", ["hostname", "address"])
def test_direct_validator_also_rejects_collapsed_inventory_or_addresses(scenario, duplicate):
    doc, vms, networks, inventory, *_ = scenario
    guests = inventory["all"]["children"]["scenario_guests"]["hosts"]
    if duplicate == "hostname":
        guests.pop(doc["instances"][1]["hostname"])
        doc["instances"][1]["hostname"] = doc["instances"][0]["hostname"]
        vms["vms"][1]["vm_name"] = vms["vms"][0]["vm_name"]
    vms["vms"][1]["ip"] = vms["vms"][1]["nics"][0]["ip"] = vms["vms"][0]["ip"]
    guests[doc["instances"][1]["hostname"]]["ansible_host"] = vms["vms"][0]["ip"]
    with pytest.raises(ValueError):
        validate_scenario_instances(doc, vms, networks, inventory)


def test_existing_bridge_replication_retains_long_bridge_name(scenario):
    doc, vms, networks, _, save, _ = scenario
    bridge = "vmbr12345678901"
    networks.clear()
    networks.update(mode="existing_bridge", bridges=[bridge])
    doc["networks"][0].update(vnet=bridge, snat=False)
    for vm in vms["vms"]:
        vm["bridge"] = vm["nics"][0]["bridge"] = bridge
    assert save().vmids == [3200, 3201, 3202]
    doc["networks"][0]["snat"] = True
    with pytest.raises(Range42Error):
        save()


def test_actual_ui_compiler_output_matches_backend_contract():
    directory = Path(__file__).parents[1] / "fixtures/replicated_scenario"
    def read(name):
        return json.loads((directory / name).read_text())
    document = read("scenario_instances.json")
    validate_scenario_instances(document, read("scenario_vms.json"), read("scenario_networks.json"),
                               yaml.safe_load((directory / "hosts.yml").read_text()))
    assert len(document["instances"]) == 3
    assert len(document["networks"]) == 2


def test_nonobject_network_file_returns_a_typed_scenario_error(scenario):
    *_, save, directory = scenario
    save()
    (directory / "manifest/scenario_networks.json").write_text("[]")
    with pytest.raises(Range42Error) as exc:
        resolve_project_scenario(directory.parents[1], scenario_label="classroom")
    assert exc.value.code == "PROJECT_SCENARIO_INVALID"
