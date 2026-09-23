"""Reviewed native firewall preferences; deployment owns intent, installation owns host authority."""
import os
from ipaddress import IPv4Network
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from app.core import runtime_operations
from app.core.errors import Range42Error
from app.core.native_sdn import NATIVE_CONTRACT
from app.core.scenario_manifest import validate_vm_manifest


class FirewallPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    prepare_management_access: StrictBool
    arm_vms: StrictBool
    ssh_sources: list[StrictStr] | None = Field(max_length=64)

    @field_validator("ssh_sources")
    @classmethod
    def sources(cls, values):
        for value in values or []:
            network = IPv4Network(value, strict=True)
            if network.prefixlen != 32 or str(network) != value:
                raise ValueError("SSH sources require canonical IPv4 /32 addresses")
        if values is not None and len(set(values)) != len(values):
            raise ValueError("Duplicate SSH sources")
        return values


def management_access_authorized() -> bool:
    return os.getenv("RANGE42_SCENARIO_MANAGEMENT_ACCESS") == "1"


def read_firewall_policy(scenario_dir: Path) -> FirewallPolicy | None:
    path = scenario_dir / "manifest/scenario_firewall.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 8192:
            raise ValueError("invalid policy file")
        policy = FirewallPolicy.model_validate_json(path.read_text())
    except (OSError, ValueError):
        raise runtime_operations.blocked("Review the scenario's firewall preferences and IPv4 /32 SSH sources.", "FIREWALL_POLICY_INVALID") from None
    if policy.prepare_management_access and not management_access_authorized():
        raise runtime_operations.blocked("This installation has not authorized scenario changes to datacenter/node management rules.", "FIREWALL_MANAGEMENT_NOT_AUTHORIZED")
    try:
        profile = runtime_operations.operation_profile("vm_firewall")
        if profile.get("contract") == NATIVE_CONTRACT:
            return policy
    except Range42Error:
        pass
    raise runtime_operations.blocked("Native scenario firewall stages require the reviewed native SDN runtime.", "FIREWALL_RUNTIME_UNSUPPORTED")


def firewall_runtime_variables(scenario_dir: Path, manifest: dict) -> dict:
    policy = read_firewall_policy(scenario_dir)
    if policy is None:
        return {}
    try:
        manifest = validate_vm_manifest(manifest)
        networks = sorted({str(IPv4Network(f"{nic['ip']}/{nic['prefix']}", strict=False))
                           for vm in manifest["vms"] for nic in vm["nics"]})
        if not networks:
            raise ValueError("No scenario NIC networks")
    except (ValueError, KeyError, TypeError):
        raise runtime_operations.blocked("Native firewall preferences require explicit v3 NIC addresses and prefixes.", "FIREWALL_POLICY_INVALID") from None
    variables = {
        "r42_fw_contract": NATIVE_CONTRACT,
        "FIREWALL_ARM_VMS": "YES" if policy.arm_vms else "NO",
        "r42_fw_prepare_management_access": policy.prepare_management_access,
        "r42_fw_scenario_networks": networks,
    }
    if policy.ssh_sources is not None:
        variables["range42_fw_vm_ssh_sources"] = policy.ssh_sources
    return variables
