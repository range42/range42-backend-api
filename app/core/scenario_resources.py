"""VM resource and ownership checks for generated concrete scenarios."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.proxmox_tls import proxmox_verify

from app.core.models import ProxmoxHost
from app.core.preflight import PreflightCheck
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data, read_proxmox_data
from app.core.scenario_manifest import validate_vm_manifest
from app.core.scenario_capacity import check_plan_capacity
from app.core.errors import Range42Error
from app.core.scenario_firewall import read_firewall_policy


def _blocked(code: str, detail: str) -> list[PreflightCheck]:
    return [PreflightCheck(check="scenario_resources", result="block", code=code,
                           detail=detail, field_path="manifest/scenario_vms.json")]


def bootstrap_features() -> set[str]:
    """Optional bootstrap inputs are unsupported unless explicitly advertised."""
    root = os.getenv("RANGE42_BUNDLE_DIR")
    try:
        if not root:
            return set()
        path = Path(root) / "proxmox/vm.bootstrap/capabilities.json"
        if path.stat().st_size > 8192:
            return set()
        capabilities = json.loads(path.read_text())
        features = capabilities.get("features")
        if capabilities.get("version") != 1 or not isinstance(features, list) or any(not isinstance(feature, str) for feature in features):
            return set()
        if capabilities.get("requires_native_contract") is not None:
            if capabilities["requires_native_contract"] is not True:
                return set()
            from app.core.bundle_runtime import runtime_snapshot
            from app.core.native_sdn import native_contract
            profile, _ = runtime_snapshot()
            if not native_contract(profile):
                return set()
        return set(features).intersection({"extra_nics", "resources", "disk_resize"})
    except (OSError, ValueError, AttributeError, Range42Error):
        return set()


def _missing_bootstrap_features(vms: list[dict]) -> set[str]:
    required = set()
    for vm in vms:
        if len(vm.get("nics") or []) > 1:
            required.add("extra_nics")
        if any(vm.get(field) is not None for field in ("cores", "memory_mb")):
            required.add("resources")
        if vm.get("disk_gb") is not None:
            required.add("disk_resize")
    return required.difference(bootstrap_features()) if required else set()


async def check_scenario_resources(scenario_dir: Path, host: ProxmoxHost | None, *,
                                   deployment_id: str, scope: str = "full",
                                   client: httpx.AsyncClient | None = None) -> list[PreflightCheck]:
    """Check generated template-based plans. Hand-authored inventory stays supported."""
    if scope == "full":
        try:
            read_firewall_policy(scenario_dir)
        except Range42Error as exc:
            return [PreflightCheck(check="scenario_firewall", result="block", code=exc.code,
                                   detail=exc.message, field_path="manifest/scenario_firewall.json")]
    try:
        path = scenario_dir / "manifest/scenario_vms.json"
        if not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 1024 * 1024:
            raise ValueError("invalid VM manifest path or size")
        vms = validate_vm_manifest(json.loads(path.read_text()))["vms"]
        if not isinstance(vms, list) or any(not isinstance(vm, dict) for vm in vms):
            raise ValueError("invalid VM list")
        if not any("template_vm_id" in vm for vm in vms):
            return []
        if any(type(vm.get("vm_id")) is not int or type(vm.get("template_vm_id")) is not int
               or not isinstance(vm.get("vm_name"), str) for vm in vms):
            raise ValueError("generated VMs require a template, integer id and name")
        if host is None:
            return _blocked("SCENARIO_RESOURCES_UNREADABLE", "Select an available target host.")
        if scope == "full" and (missing := _missing_bootstrap_features(vms)):
            return _blocked("BOOTSTRAP_CAPABILITY_MISSING", "This runtime cannot apply these VM choices: " + ", ".join(sorted(missing)) + ". Use one NIC and inherit unsupported resource sizes from the template, or select a runtime that supports these inputs.")
        if client is None:
            async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned_client:
                return await check_scenario_resources(scenario_dir, host, deployment_id=deployment_id,
                                                       scope=scope, client=owned_client)
        resources = await list_proxmox_data(client, host, "/cluster/resources", params={"type": "vm"})
        by_id = {int(vm["vmid"]): vm for vm in resources}
        required_memory = 0
        for planned in vms:
            vmid = planned["vm_id"]
            existing = by_id.get(vmid)
            if existing is None and scope in ("full", "teardown"):
                # Resource lists are filtered by VM.Audit. nextid checks global
                # occupancy even when the token cannot see an existing VM.
                available = await read_proxmox_data(client, host, "/cluster/nextid", params={"vmid": vmid})
                if str(available) != str(vmid):
                    return _blocked("SCENARIO_RESOURCES_UNREADABLE", f"Proxmox did not confirm VMID {vmid} is unused.")
            if scope == "full":
                if existing:
                    return _blocked("VMID_IN_USE", f"VMID {vmid} already exists. Use configuration for an owned VM, or select an unused VMID.")
                template = by_id.get(planned["template_vm_id"])
                if (not template or template.get("template") != 1 or template.get("node") != host.node_name
                        or template.get("type") != "qemu"):
                    return _blocked("TEMPLATE_NOT_READY", f"Template {planned['template_vm_id']} must be a QEMU template on {host.node_name}.")
                memory = planned["memory_mb"] * 1024**2 if planned.get("memory_mb") is not None else template.get("maxmem")
                if type(memory) not in (int, float) or memory <= 0:
                    return _blocked("SCENARIO_RESOURCES_UNREADABLE", "Proxmox did not report template memory requirements.")
                required_memory += memory
            else:
                if existing is None and scope == "teardown":
                    continue
                if (not existing or existing.get("node") != host.node_name or existing.get("name") != planned["vm_name"]
                        or existing.get("type") != "qemu" or existing.get("template")):
                    return _blocked("VM_OWNERSHIP_MISMATCH", f"VMID {vmid} does not match the deployment's VM name, type and target node.")
                config = await read_proxmox_data(client, host, f"/nodes/{quote(host.node_name, safe='')}/qemu/{vmid}/config")
                if not isinstance(config, dict) or f"range42-deployment:{deployment_id}" not in str(config.get("description", "")).splitlines():
                    return _blocked("VM_OWNERSHIP_MISMATCH", f"VMID {vmid} is missing this deployment's ownership marker. It will not be configured or removed.")
        if scope == "full":
            checks = await check_plan_capacity(client, host, vms, required_memory=required_memory)
            return [PreflightCheck(check="scenario_resources", result="pass", detail="Template and free VMID checks passed."), *checks]
        return [PreflightCheck(check="scenario_resources", result="pass", detail="Template and free VMID checks passed." if scope == "full" else "Deployment VM ownership verified.")]
    except ProxmoxReadError as exc:
        return _blocked("SCENARIO_RESOURCES_UNREADABLE", str(exc))
    except (ValueError, TypeError, KeyError, OSError):
        return _blocked("SCENARIO_RESOURCES_INVALID", "The VM manifest or target resource data is invalid.")
