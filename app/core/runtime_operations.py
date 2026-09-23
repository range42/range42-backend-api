"""Plan allowlisted composite operations against observed deployment resources."""
from __future__ import annotations

import re
import json
import os
from pathlib import Path

from pydantic import TypeAdapter

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.bundle_runtime import dependencies, runtime_snapshot
from app.schemas.v1.runtime import RuntimeOperation

NATIVE_OPERATIONS = ("vm_firewall", "scenario_firewall", "sdn_snat", "host_firewall", "runtime_observe", "sdn_network", "firewall_alias", "firewall_rule")


def blocked(message: str, code: str = "RUNTIME_OPERATION_BLOCKED") -> Range42Error:
    return Range42Error(status=409, code=code, error="runtime_operation_blocked", message=message)


def target_identity(host: ProxmoxHost | None) -> dict[str, str]:
    if host is None:
        raise blocked("The deployment's target host is unavailable", "RUNTIME_TARGET_CHANGED")
    return {"api_url": host.api_url.rstrip("/"), "node_name": host.node_name}


def _controller_exact_snat(profile: dict) -> bool:
    for directory in profile["environment"].get("ANSIBLE_ROLES_PATH", "").split(os.pathsep):
        if not directory:
            continue
        role = Path(directory) / "range42-ansible_roles-proxmox_controller"
        if not role.is_dir():
            continue
        # The first installed role wins Ansible's search order. A later safe
        # copy cannot authorize an older role that will actually be imported.
        marker = role / "runtime-capabilities.json"
        try:
            if marker.is_symlink() or marker.stat().st_size > 8192:
                return False
            capabilities = json.loads(marker.read_text())
            return (isinstance(capabilities, dict) and capabilities.get("version") == 1
                    and capabilities.get("snat_rule_matching") == "exact_source_nat_target_v1")
        except (OSError, ValueError, TypeError):
            return False
    return False


def operation_profile(kind: str) -> dict:
    profile, fingerprint = runtime_snapshot()
    from app.core.native_sdn import native_contract
    contract = native_contract(profile)
    if contract and kind in NATIVE_OPERATIONS:
        return {"fingerprint": fingerprint, "dependencies": dependencies(profile), "contract": contract,
                "operations": list(NATIVE_OPERATIONS)}
    if kind not in ("vm_firewall", "scenario_firewall", "sdn_snat"):
        raise blocked("This operation requires the reviewed native SDN runtime", "RUNTIME_CAPABILITY_MISSING")
    try:
        root = Path(profile["environment"]["RANGE42_BUNDLE_DIR"])
        marker = root / "runtime-capabilities.json"
        if marker.is_symlink() or marker.stat().st_size > 8192:
            raise ValueError("invalid capabilities")
        capabilities = json.loads(marker.read_text())
        if (not isinstance(capabilities, dict) or capabilities.get("version") != 1
                or not isinstance(capabilities.get("operations"), list) or kind not in capabilities["operations"]
                or (kind == "sdn_snat" and (capabilities.get("snat_reconciles_all_declared_subnets") is not True
                                          or not _controller_exact_snat(profile)))):
            raise ValueError("unsupported operation")
    except (OSError, ValueError, TypeError):
        raise blocked("Install a matching runtime release with this operation's reviewed composite bundles", "RUNTIME_CAPABILITY_MISSING") from None
    operations = [name for name in ("vm_firewall", "scenario_firewall", "sdn_snat")
                  if name in capabilities["operations"] and
                  (name != "sdn_snat" or (capabilities.get("snat_reconciles_all_declared_subnets") is True
                                         and _controller_exact_snat(profile)))]
    return {"fingerprint": fingerprint, "dependencies": dependencies(profile), "operations": operations}


def plan_operation(request: dict, state: dict) -> dict:
    operation = TypeAdapter(RuntimeOperation).validate_python(request)
    plan = {"vmids": [], "missing_vmids": [], "variables": {}}
    if operation.kind == "sdn_network":
        from app.core.runtime_networks import network_plan
        return network_plan(request, state["network_lifecycle"])
    if operation.kind == "runtime_observe":
        return {**plan, "bundle": "firewall/in_proxmox/firewall.report.status", "read_only": True}
    if operation.kind == "host_firewall":
        before = {key: state.get("firewall", {}).get(key) for key in ("datacenter_enabled", "node_enabled")}
        if any(type(value) is not bool for value in before.values()) or state["firewall"].get("errors"):
            raise blocked("Read both host firewall switches before requesting a change")
        return {**plan, "bundle": f"firewall/in_proxmox/firewall.{'enable' if operation.enabled else 'disable'}.datacenter_and_nodes",
                "before": before, "shared_scope": "datacenter_and_selected_node"}
    if operation.kind == "sdn_snat":
        if state["sdn"]["pending_changes"] is not False or state["sdn"]["errors"]:
            raise blocked("SDN apply requires verified absence of pending changes across the entire cluster", "SDN_PENDING_CHANGES")
        network = next((network for network in state["networks"] if network["vnet"] == operation.vnet), None)
        if (not network or not network["identity_matches"] or not network["active"]
                or type(network["configured_snat"]) is not bool
                or not isinstance(network["subnet_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]+", network["subnet_id"])):
            raise blocked("The selected subnet must be declared by this deployment and active with its original zone, CIDR and gateway")
        plan.update(bundle=f"proxmox/sdn_network.internet_{'on' if operation.enabled else 'off'}",
                    variables={"BUNDLE_SDN_SUBNET_ID": network["subnet_id"]}, subnet=network["subnet"])
        return plan
    guests = state["vms"]
    if operation.kind == "vm_firewall":
        guests = [guest for guest in guests if guest["vm_id"] == operation.vm_id]
        if len(guests) != 1 or guests[0]["status"] != "owned":
            raise blocked("The selected VM must exist and carry this deployment's exact ownership marker")
        plan["variables"] = {"BUNDLE_VM_ID": operation.vm_id}
    elif any(guest["status"] not in ("owned", "missing") for guest in guests):
        raise blocked("The scenario contains a foreign or unreadable VM; no guest firewall will be changed")
    owned = [guest for guest in guests if guest["status"] == "owned"]
    if not owned:
        raise blocked("There are no verified deployed guests to update")
    if operation.enabled and any(not guest["nics"] for guest in owned):
        raise blocked("Every guest must have a network card before its firewall can be armed")
    plan["vmids"] = [guest["vm_id"] for guest in owned]
    plan["missing_vmids"] = [guest["vm_id"] for guest in guests if guest["status"] == "missing"]
    target = "vm" if operation.kind == "vm_firewall" else "vms"
    plan["bundle"] = f"firewall/in_proxmox/firewall.{'enable' if operation.enabled else 'disable'}.{target}"
    return plan
