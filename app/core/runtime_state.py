"""Read-only deployment firewall and SDN observations; never infer ownership."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data as _list, read_proxmox_data as _read
from app.core.proxmox_tls import proxmox_verify
from app.core.scenario_manifest import validate_vm_manifest
from app.core.scenario_networks import (
    NetworkBlocked, SdnPlan, _check_global_pending, _check_pending, _read_plan, _subnet_cidr,
)


def runtime_targets(scenario_dir: Path) -> tuple[list[dict], SdnPlan | None]:
    try:
        path = scenario_dir / "manifest/scenario_vms.json"
        if path.is_symlink() or not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 1048576:
            raise ValueError("invalid manifest")
        vms = validate_vm_manifest(json.loads(path.read_text()))["vms"]
        if not isinstance(vms, list) or len(vms) > 512:
            raise ValueError("invalid guest list")
        for vm in vms:
            if (not isinstance(vm, dict) or type(vm.get("vm_id")) is not int
                    or not 100 <= vm["vm_id"] <= 999999999
                    or not isinstance(vm.get("vm_name"), str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,62}", vm["vm_name"])):
                raise ValueError("guest identity is required")
        if len({vm["vm_id"] for vm in vms}) != len(vms):
            raise ValueError("duplicate guest identity")
        plan = _read_plan(scenario_dir)
        return vms, plan if isinstance(plan, SdnPlan) else None
    except (OSError, ValueError, KeyError, TypeError):
        raise Range42Error(code="RUNTIME_MANIFEST_INVALID", error="runtime_manifest_invalid", status=409,
                           message="Runtime controls require bounded VM names and IDs in the pinned scenario manifest") from None


def _flag(value) -> bool | None:
    if value in (0, "0", False):
        return False
    if value in (1, "1", True):
        return True
    return None


def _nics(config: dict) -> list[dict]:
    result = []
    for key, value in config.items():
        if not re.fullmatch(r"net(?:[0-9]|[12][0-9]|3[01])", key):
            continue
        if not isinstance(value, str):
            raise ValueError("invalid NIC configuration")
        fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
        result.append({"index": int(key[3:]), "bridge": fields.get("bridge"),
                       "firewall_enabled": _flag(fields.get("firewall", "0"))})
    return sorted(result, key=lambda nic: nic["index"])


async def _guests(client, host, vms, deployment_id, dc_enabled) -> list[dict]:
    try:
        resources = await _list(client, host, "/cluster/resources", params={"type": "vm"})
        existing = {int(row["vmid"]): row for row in resources}
    except (ProxmoxReadError, ValueError, KeyError, TypeError):
        return [{"vm_id": vm["vm_id"], "name": vm["vm_name"], "status": "unavailable",
                 "error": "Cannot read the target guest inventory"} for vm in vms]
    results = []
    for vm in vms:
        row = {"vm_id": vm["vm_id"], "name": vm["vm_name"], "status": "unavailable",
               "firewall_enabled": None, "nics": [], "filtering_configured": None}
        try:
            live = existing.get(vm["vm_id"])
            if live is None:
                vacant = await _read(client, host, "/cluster/nextid", params={"vmid": vm["vm_id"]})
                if str(vacant) != str(vm["vm_id"]):
                    raise ValueError("absence not confirmed")
                row["status"] = "missing"
            elif (live.get("node") != host.node_name or live.get("name") != vm["vm_name"]
                  or live.get("type") != "qemu" or live.get("template")):
                row["status"] = "conflict"
            else:
                base = f"/nodes/{quote(host.node_name, safe='')}/qemu/{vm['vm_id']}"
                config = await _read(client, host, base + "/config")
                if not isinstance(config, dict):
                    raise ValueError("invalid guest config")
                if (config.get("name") != vm["vm_name"] or _flag(config.get("template", 0)) is not False
                        or f"range42-deployment:{deployment_id}" not in str(config.get("description", "")).splitlines()):
                    row["status"] = "conflict"
                else:
                    row["status"] = "owned"
                    options = await _read(client, host, base + "/firewall/options")
                    if not isinstance(options, dict):
                        raise ValueError("invalid firewall options")
                    row["firewall_enabled"] = _flag(options.get("enable"))
                    row["nics"] = _nics(config)
                    prerequisites = [dc_enabled, row["firewall_enabled"],
                                     *[nic["firewall_enabled"] for nic in row["nics"]]]
                    row["filtering_configured"] = (all(prerequisites) and bool(row["nics"])) if None not in prerequisites else None
        except (ProxmoxReadError, ValueError, TypeError):
            row["status"] = "unavailable"
            row["error"] = "Cannot verify this guest's ownership and firewall state"
        results.append(row)
    return results


async def _networks(client, host, plan) -> tuple[dict, list[dict]]:
    sdn = {"pending_changes": None, "errors": []}
    if plan is None:
        return sdn, []
    result = [{"vnet": desired.vnet, "zone": plan.zone, "subnet": desired.subnet,
               "gateway": desired.gateway, "manifest_snat": desired.snat, "configured_snat": None,
               "subnet_id": None, "identity_matches": False, "active": False,
               "live_forwarding_verified": False} for desired in plan.vnets]
    try:
        zones = await _list(client, host, "/cluster/sdn/zones", params={"pending": 1})
        vnets = await _list(client, host, "/cluster/sdn/vnets", params={"pending": 1})
        subnets = {}
        for vnet in vnets:
            name = vnet.get("vnet")
            if not isinstance(name, str):
                raise ValueError("missing VNet name")
            subnets[name] = await _list(client, host, f"/cluster/sdn/vnets/{quote(name, safe='')}/subnets", params={"pending": 1})
        try:
            _check_pending(zones + vnets + [row for rows in subnets.values() for row in rows])
            await _check_global_pending(host, client)
            sdn["pending_changes"] = False
        except NetworkBlocked as exc:
            sdn["pending_changes"] = True if exc.check.code == "SDN_PENDING_CHANGES" else None
            sdn["errors"].append(exc.check.detail)
        runtime = f"/nodes/{quote(host.node_name, safe='')}/sdn/zones"
        active_zones = await _list(client, host, runtime)
        active_vnets = await _list(client, host, f"{runtime}/{quote(plan.zone, safe='')}/content")
        zone = next((row for row in zones if row.get("zone") == plan.zone), {})
        members = zone.get("nodes") or []
        members = members.split(",") if isinstance(members, str) else members
        zone_matches = zone.get("type") == "simple" and (not members or host.node_name in members)
        for item in result:
            live = next((row for row in vnets if row.get("vnet") == item["vnet"]), {})
            matches = [row for row in subnets.get(item["vnet"], []) if str(_subnet_cidr(row)) == item["subnet"]]
            subnet = matches[0] if len(matches) == 1 else {}
            item["subnet_id"] = subnet.get("subnet")
            item["identity_matches"] = bool(zone_matches and live.get("zone") == plan.zone and subnet
                                            and subnet.get("gateway") == item["gateway"])
            item["configured_snat"] = _flag(subnet.get("snat", 0)) if subnet else None
            item["active"] = any(row.get("zone") == plan.zone and row.get("status") == "available" for row in active_zones) and any(
                row.get("vnet") == item["vnet"] and row.get("status") == "available" for row in active_vnets)
    except (ProxmoxReadError, ValueError, TypeError):
        sdn["pending_changes"] = None
        sdn["errors"].append("Cannot verify the cluster's complete SDN state")
    return sdn, result


async def read_runtime_state(scenario_dir: Path, host: ProxmoxHost, *, deployment_id: str,
                             client: httpx.AsyncClient | None = None) -> dict:
    vms, plan = runtime_targets(scenario_dir)
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await read_runtime_state(scenario_dir, host, deployment_id=deployment_id, client=owned)
    firewall = {"datacenter_enabled": None, "node_enabled": None, "errors": []}
    for key, path in (("datacenter_enabled", "/cluster/firewall/options"),
                      ("node_enabled", f"/nodes/{quote(host.node_name, safe='')}/firewall/options")):
        try:
            options = await _read(client, host, path)
            if not isinstance(options, dict):
                raise ValueError("invalid options")
            firewall[key] = _flag(options.get("enable"))
        except (ProxmoxReadError, ValueError):
            firewall["errors"].append(f"Cannot read {key} on the selected host")
    guests = await _guests(client, host, vms, deployment_id, firewall["datacenter_enabled"])
    sdn, networks = await _networks(client, host, plan)
    return {"deployment_id": deployment_id, "target_host_id": host.id, "node_name": host.node_name,
            "firewall": firewall, "vms": guests, "sdn": sdn, "networks": networks}


async def read_runtime_report(scenario_dir: Path, host: ProxmoxHost, *, deployment_id: str,
                              client: httpx.AsyncClient | None = None) -> dict:
    from app.core.runtime_reports import report_from_state
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await read_runtime_report(scenario_dir, host, deployment_id=deployment_id, client=owned)
    state = await read_runtime_state(scenario_dir, host, deployment_id=deployment_id, client=client)
    return await report_from_state(client, host, state)
