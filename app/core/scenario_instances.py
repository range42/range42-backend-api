"""Validate authored replication against its literal VM, network and inventory files.

This manifest describes identities, not allocation ownership. It cannot authorize
reuse of a VM or release an allocation; those require a trusted deployment ledger.
"""
from __future__ import annotations

import hashlib
from ipaddress import IPv4Address, IPv4Network
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.core.scenario_manifest import Vm, VmId, validate_vm_manifest
from app.core.scenario_networks import BridgeName, BridgePlan, SdnPlan, VNet

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
Scope = Literal["shared", "per_team", "per_user"]


class StrictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class User(StrictDocument):
    id: Identifier


class Team(User):
    users: list[User] = Field(max_length=64)


class Intent(StrictDocument):
    teams: list[Team] = Field(max_length=64)
    node_scopes: dict[Identifier, Scope] = Field(min_length=1, max_length=64)
    network_scopes: dict[Identifier, Scope] = Field(min_length=1, max_length=32)


class Identity(StrictDocument):
    instance_key: str = Field(pattern=r"^(?:vm|net)-[0-9a-f]{64}$")
    source_node_id: Identifier
    team_id: Identifier | None
    user_id: Identifier | None


class InstanceNic(StrictDocument):
    index: Annotated[StrictInt, Field(ge=0, le=31)]
    nic_key: Identifier
    network_instance_key: str = Field(pattern=r"^net-[0-9a-f]{64}$")


class Instance(Identity):
    vm_id: VmId
    hostname: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
    nics: list[InstanceNic] = Field(min_length=1, max_length=32)


class Network(Identity, VNet):
    vnet: BridgeName


class Instances(StrictDocument):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    scenario_id: Identifier
    intent: Intent
    instances: list[Instance] = Field(min_length=1, max_length=64)
    networks: list[Network] = Field(min_length=1, max_length=32)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identities(document: Instances, scopes: dict[str, Scope], rows: list, kind: str, limit: int) -> None:
    expected = set()
    for source, scope in scopes.items():
        if scope == "shared":
            cohorts = [(None, None)]
        elif scope == "per_team":
            cohorts = [(team.id, None) for team in document.intent.teams]
        else:
            cohorts = [(team.id, user.id) for team in document.intent.teams for user in team.users]
        _require(bool(cohorts), "Replicated sources require an explicit nonempty roster")
        expected.update((source, team, user) for team, user in cohorts)
        _require(len(expected) <= limit, "Replication exceeds supported resource bounds")
    actual = {(row.source_node_id, row.team_id, row.user_id) for row in rows}
    _require(len(actual) == len(rows) and actual == expected, "Instances must exactly match the replication roster and scopes")
    for row in rows:
        # ASCII identities and compact JSON match the UI's UTF-8 JSON.stringify.
        encoded = json.dumps([document.scenario_id, row.source_node_id, row.team_id, row.user_id], separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        _require(row.instance_key == f"{kind}-{digest}", "Instance keys must match their stable source and cohort")


def _inventory_hosts(inventory: dict, *, max_hosts: int) -> dict:
    """Visit bounded groups and reject aliases that can overwrite a literal host."""
    hosts = {}

    def visit(group, depth):
        _require(depth <= 16 and isinstance(group, dict), "Invalid inventory group")
        entries = group.get("hosts", {})
        children = group.get("children", {})
        _require(isinstance(entries, dict) and isinstance(children, dict), "Invalid inventory host mapping")
        for name, values in entries.items():
            _require(name not in hosts and isinstance(values, dict), "Inventory hosts must have one literal definition")
            hosts[name] = values
            _require(len(hosts) <= max_hosts, "Inventory exceeds declared host bounds")
        for child in children.values():
            visit(child, depth + 1)

    for group in inventory.values():
        visit(group, 0)
    return hosts


def validate_vm_inventory(vm_manifest: dict, inventory: dict) -> None:
    """Bind every current concrete VM to one literal management inventory host."""
    if vm_manifest.get("version") != 3:
        return  # Older manifests may carry VMIDs without guest names/addresses.
    vms = [Vm.model_validate(vm) for vm in vm_manifest["vms"]]
    all_hosts = _inventory_hosts(inventory, max_hosts=len(vms) + 2)
    guests = inventory.get("all", {}).get("children", {}).get("scenario_guests", {}).get("hosts", {})
    names = {vm.vm_name for vm in vms}
    _require(isinstance(guests, dict) and set(guests) == names
             and set(all_hosts) - {"r42-proxmox", "r42-proxmox-cli"} == names,
             "Inventory must contain exactly the declared scenario guests")
    for vm in vms:
        _require(guests[vm.vm_name].get("ansible_host") == str(vm.ip),
                 "Inventory management addresses must match their VM manifest")
        if vm.cloud_init is not None:
            _require(guests[vm.vm_name].get("ansible_user") == vm.cloud_init.ssh_user,
                     "Inventory SSH users must match their cloud-init preferences")


def validate_scenario_instances(document: dict, vm_manifest: dict, network_manifest: dict, inventory: dict) -> None:
    _require(isinstance(network_manifest, dict), "Network manifest must be an object")
    plan = Instances.model_validate(document)
    teams = plan.intent.teams
    _require(sum(len(team.users) for team in teams) <= 64, "Replication rosters support at most 64 users")
    _require(len({team.id for team in teams}) == len(teams), "Team identities must be unique")
    _require(all(len({user.id for user in team.users}) == len(team.users) for team in teams), "User identities must be unique within their team")
    _require(not set(plan.intent.node_scopes).intersection(plan.intent.network_scopes), "VM and network source identities must differ")
    _identities(plan, plan.intent.node_scopes, plan.instances, "vm", 64)
    _identities(plan, plan.intent.network_scopes, plan.networks, "net", 32)
    _require(sum(len(vm.nics) for vm in plan.instances) <= 256, "Replication exceeds the total NIC limit")
    _require(vm_manifest.get("version") == 3, "Replicated scenarios require VM manifest version 3")
    validate_vm_manifest(vm_manifest)
    vms = [Vm.model_validate(vm) for vm in vm_manifest["vms"]]
    by_id = {vm.vm_id: vm for vm in vms}
    _require(len(by_id) == len(vms) == len(plan.instances)
             and {vm.vm_id for vm in plan.instances} == set(by_id), "VM manifest must contain every instance exactly once")
    network_by_key = {network.instance_key: network for network in plan.networks}
    _require(len({network.vnet for network in plan.networks}) == len(plan.networks), "Network names must be unique")
    subnets = [IPv4Network(network.subnet) for network in plan.networks]
    _require(not any(a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1:]),
             "Replicated network subnets must not overlap")
    declared = [{name: getattr(network, name) for name in ("vnet", "subnet", "gateway", "snat")} for network in plan.networks]
    if network_manifest.get("mode") == "sdn":
        actual_networks = SdnPlan.model_validate(network_manifest)
        _require(sorted(declared, key=lambda row: row["vnet"]) == sorted(
            [row.model_dump() for row in actual_networks.vnets], key=lambda row: row["vnet"]),
            "Network manifest must match every declared instance")
    else:
        actual_bridges = BridgePlan.model_validate(network_manifest)
        _require(len(actual_bridges.bridges) == len(plan.networks)
                 and set(actual_bridges.bridges) == {network.vnet for network in plan.networks},
                 "Bridge manifest must match every declared instance")
        _require(all(not network.snat for network in plan.networks), "Existing bridges cannot declare managed SNAT")
    source_nics = {}
    for instance in plan.instances:
        vm = by_id[instance.vm_id]
        _require(vm.vm_name == instance.hostname, "Instance hostname must match the VM manifest")
        _require([nic.index for nic in instance.nics] == list(range(len(vm.nics)))
                 and len({nic.nic_key for nic in instance.nics}) == len(instance.nics), "Every NIC needs one stable source identity")
        source_links = []
        for nic, literal in zip(instance.nics, vm.nics, strict=True):
            network = network_by_key.get(nic.network_instance_key)
            _require(network is not None, "NIC references an unknown network instance")
            _require((network.team_id is None or network.team_id == instance.team_id)
                     and (network.user_id is None or network.user_id == instance.user_id),
                     "NIC cannot connect outside its team or user cohort")
            subnet = IPv4Network(network.subnet)
            _require(literal.bridge == network.vnet and literal.prefix == subnet.prefixlen,
                     "NIC bridge and prefix must match its network instance")
            _require(literal.ip in subnet and literal.ip not in (subnet.network_address, subnet.broadcast_address)
                     and str(literal.ip) != network.gateway, "NIC must use a guest address inside its network")
            expected_gateway = IPv4Address(network.gateway) if network.gateway and nic.index == 0 else None
            _require(literal.gateway == expected_gateway, "Only the management NIC may use the declared gateway")
            source_links.append((nic.index, nic.nic_key, network.source_node_id))
        previous = source_nics.setdefault(instance.source_node_id, source_links)
        _require(source_links == previous, "All replicas must preserve source NIC identities and connections")
    validate_vm_inventory(vm_manifest, inventory)


def _read_document(path: Path, scenario_dir: Path) -> dict:
    _require(not path.is_symlink() and path.resolve().is_relative_to(scenario_dir.resolve())
             and path.is_file() and path.stat().st_size <= 262144,
             "Instance declarations must be bounded files inside their scenario")
    return json.loads(path.read_text())


def validate_instance_files(scenario_dir: Path, vm_manifest: dict, inventory: dict) -> None:
    path = scenario_dir / "manifest/scenario_instances.json"
    if not path.exists() and not path.is_symlink():
        return
    validate_scenario_instances(_read_document(path, scenario_dir), vm_manifest,
                               _read_document(scenario_dir / "manifest/scenario_networks.json", scenario_dir), inventory)
