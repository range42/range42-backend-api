"""Read-only checks for the concrete scenario network manifest and target.

The emitter uses Hyde's ``sdn_network.bootstrap`` contract. New networks may be
created by a full attempt; configure needs an already active network. Existing
objects with another owner/shape and pending changes are never reconciled here.
"""
from __future__ import annotations

import os
import re
from ipaddress import IPv4Address, IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

import httpx

from app.core.proxmox_tls import proxmox_verify
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, model_validator

from app.core.models import ProxmoxHost
from app.core.preflight import PreflightCheck
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data as _list, read_proxmox_data as _read

SdnName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,7}$")]
BridgeName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")]


class VNet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vnet: SdnName
    subnet: str
    gateway: str | None = None
    snat: StrictBool

    @model_validator(mode="after")
    def validate_addresses(self):
        network = IPv4Network(self.subnet)
        if self.gateway is not None:
            gateway = IPv4Address(self.gateway)
            if gateway not in network or gateway in (network.network_address, network.broadcast_address):
                raise ValueError("gateway must be a usable address inside the subnet")
        if self.snat and self.gateway is None:
            raise ValueError("SNAT requires a gateway")
        return self


class SdnPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["sdn"]
    zone: SdnName
    vnets: list[VNet] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_networks(self):
        if len({v.vnet for v in self.vnets}) != len(self.vnets):
            raise ValueError("VNet names must be unique")
        networks = [IPv4Network(v.subnet) for v in self.vnets]
        if any(a.overlaps(b) for i, a in enumerate(networks) for b in networks[i + 1:]):
            raise ValueError("VNet subnets must not overlap on the target node")
        return self


class BridgePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["existing_bridge"]
    bridges: list[BridgeName] = Field(min_length=1, max_length=64)


_PLAN = TypeAdapter(Annotated[SdnPlan | BridgePlan, Field(discriminator="mode")])


class NetworkBlocked(Exception):
    def __init__(self, code: str, detail: str):
        self.check = PreflightCheck(check="scenario_networks", result="block", code=code,
                                   detail=detail, field_path="manifest/scenario_networks.json")


def _read_plan(scenario_dir: Path) -> SdnPlan | BridgePlan | None:
    path = scenario_dir / "manifest/scenario_networks.json"
    if not path.exists() and not path.is_symlink():
        return None
    if not path.resolve().is_relative_to(scenario_dir.resolve()) or path.stat().st_size > 65536:
        raise ValueError("network manifest must be a small file inside the scenario")
    return _PLAN.validate_json(path.read_text())


def _check_pending(rows: list[dict]) -> None:
    if any(row.get("pending") or row.get("state") not in (None, "unchanged") for row in rows):
        raise NetworkBlocked("SDN_PENDING_CHANGES",
                             "The cluster has pending SDN changes. Review and apply or discard them in Proxmox before deployment.")


def _subnet_cidr(row: dict) -> IPv4Network | IPv6Network:
    cidr = row.get("cidr")
    if not cidr:
        match = re.search(r"-([0-9a-fA-F.:]+)-(\d+)$", str(row.get("subnet", "")))
        if not match:
            raise ValueError("unrecognized SDN subnet")
        cidr = f"{match[1]}/{match[2]}"
    return ip_network(cidr)


def _check_interface_overlap(desired: VNet, interfaces: list[dict]) -> None:
    network = IPv4Network(desired.subnet)
    for interface in interfaces:
        if interface.get("iface") == desired.vnet:
            continue
        cidr = interface.get("cidr")
        if not cidr and interface.get("address") and interface.get("netmask"):
            cidr = f"{interface['address']}/{interface['netmask']}"
        if cidr:
            existing = ip_network(cidr, strict=False)
            if existing.version == network.version and existing.overlaps(network):
                raise NetworkBlocked("SDN_CONFLICT", f"Subnet {desired.subnet} overlaps another target interface. Choose a subnet outside management and existing bridge networks.")


async def _check_global_pending(host: ProxmoxHost, client: httpx.AsyncClient) -> None:
    """Apply commits every SDN family, including objects outside this scenario."""
    _check_pending(await _list(client, host, "/cluster/sdn/controllers", params={"pending": 1}))
    features = {row.get("id") for row in await _list(client, host, "/cluster/sdn")}
    for feature, endpoint in (("prefix-lists", "prefix-lists"), ("route-maps", "route-maps/entries")):
        if feature in features:
            _check_pending(await _list(client, host, f"/cluster/sdn/{endpoint}", params={"pending": 1}))
    if "fabrics" in features:
        # Fabric node results are filtered by Sys.Audit, even with SDN.Allocate.
        permissions = await _read(client, host, "/access/permissions", params={"path": "/nodes"})
        if not isinstance(permissions, dict) or not permissions.get("/nodes", {}).get("Sys.Audit"):
            raise NetworkBlocked("SDN_PERMISSION_REQUIRED", "Global SDN apply needs Sys.Audit on /nodes to verify that no fabric node changes are pending.")
        data = await _read(client, host, "/cluster/sdn/fabrics/all", params={"pending": 1})
        if not isinstance(data, dict):
            raise ValueError("invalid fabric data")
        for key in ("fabrics", "nodes"):
            rows = data.get(key)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("invalid fabric list")
            _check_pending(rows)


async def _check_sdn(plan: SdnPlan, host: ProxmoxHost, scope: str,
                     client: httpx.AsyncClient) -> PreflightCheck:
    if scope == "full":
        permissions = await _read(client, host, "/access/permissions", params={"path": "/sdn"})
        if not isinstance(permissions, dict) or not permissions.get("/sdn", {}).get("SDN.Allocate"):
            raise NetworkBlocked("SDN_PERMISSION_REQUIRED", "The selected API token needs SDN.Allocate on /sdn to prepare and apply networks.")
    zones = await _list(client, host, "/cluster/sdn/zones", params={"pending": 1})
    vnets = await _list(client, host, "/cluster/sdn/vnets", params={"pending": 1})
    _check_pending(zones + vnets)
    if scope == "full":
        await _check_global_pending(host, client)
    interfaces = await _list(client, host, f"/nodes/{quote(host.node_name, safe='')}/network")
    interfaces_by_name = {row.get("iface"): row for row in interfaces}
    zone = next((row for row in zones if row.get("zone") == plan.zone), None)
    if zone:
        nodes = zone.get("nodes", "")
        nodes = [node for node in nodes.split(",") if node] if isinstance(nodes, str) else nodes
        if zone.get("type") != "simple" or (nodes and host.node_name not in nodes):
            raise NetworkBlocked("SDN_CONFLICT", "The selected zone has another type or does not include the target node. Choose another zone.")
    elif scope != "full":
        raise NetworkBlocked("SDN_NOT_READY", "The declared SDN zone does not exist. Run full deployment before configuration.")

    all_subnets = []
    for row in vnets:
        name = row.get("vnet")
        if not isinstance(name, str):
            raise NetworkBlocked("SCENARIO_NETWORK_UNREADABLE", "Proxmox returned a VNet without a name.")
        subnets = await _list(client, host, f"/cluster/sdn/vnets/{quote(name, safe='')}/subnets", params={"pending": 1})
        _check_pending(subnets)
        all_subnets.extend((name, subnet) for subnet in subnets)

    create = []
    runtime_vnets = None
    for desired in plan.vnets:
        _check_interface_overlap(desired, interfaces)
        existing = next((row for row in vnets if row.get("vnet") == desired.vnet), None)
        interface = interfaces_by_name.get(desired.vnet)
        if existing and existing.get("zone") != plan.zone:
            raise NetworkBlocked("SDN_CONFLICT", f"VNet {desired.vnet} already belongs to another zone.")
        if not existing and interface:
            raise NetworkBlocked("SDN_CONFLICT", f"Interface {desired.vnet} already exists outside the declared SDN zone.")
        if not existing:
            if scope != "full":
                raise NetworkBlocked("SDN_NOT_READY", f"VNet {desired.vnet} is missing. Run full deployment first.")
            create.append(desired.vnet)
        else:
            # Generated SDN bridges are omitted from /nodes/{node}/network.
            # The node's SDN status is authoritative for applied zone/VNet state.
            if runtime_vnets is None:
                runtime_root = f"/nodes/{quote(host.node_name, safe='')}/sdn/zones"
                runtime_zones = await _list(client, host, runtime_root)
                if not any(row.get("zone") == plan.zone and row.get("status") == "available"
                           for row in runtime_zones):
                    raise NetworkBlocked("SDN_NOT_READY", f"SDN zone {plan.zone} is not available on the target. Apply and verify SDN in Proxmox.")
                runtime_rows = await _list(client, host, f"{runtime_root}/{quote(plan.zone, safe='')}/content")
                runtime_vnets = {row.get("vnet"): row for row in runtime_rows}
            if runtime_vnets.get(desired.vnet, {}).get("status") != "available":
                raise NetworkBlocked("SDN_NOT_READY", f"VNet {desired.vnet} is not available on the target. Apply and verify SDN in Proxmox.")

        found = False
        for vnet, subnet in all_subnets:
            network = _subnet_cidr(subnet)
            if network.version != 4 or not network.overlaps(IPv4Network(desired.subnet)):
                continue
            if vnet != desired.vnet or network != IPv4Network(desired.subnet):
                raise NetworkBlocked("SDN_CONFLICT", f"Subnet {desired.subnet} overlaps another configured subnet.")
            found = True
            if subnet.get("gateway") != desired.gateway or bool(int(subnet.get("snat", 0))) != desired.snat:
                raise NetworkBlocked("SDN_CONFLICT", f"Subnet {desired.subnet} has different gateway or SNAT settings. Review the existing network before changing it.")
        if existing and not found:
            if scope != "full":
                raise NetworkBlocked("SDN_NOT_READY", f"Subnet {desired.subnet} is missing from {desired.vnet}.")
            create.append(f"{desired.vnet} subnet")
    detail = f"Full deployment will create: {', '.join(create)}." if create else "Declared SDN networks are active and match the plan."
    return PreflightCheck(check="scenario_networks", result="pass", detail=detail)


async def check_scenario_networks(scenario_dir: Path, host: ProxmoxHost | None, *,
                                   scope: str = "full", client: httpx.AsyncClient | None = None) -> list[PreflightCheck]:
    """Validate the optional network declaration; perform no writes to Proxmox."""
    try:
        plan = _read_plan(scenario_dir)
        if plan is None or scope == "teardown":
            return []
        if host is None:
            raise NetworkBlocked("SCENARIO_NETWORK_UNREADABLE", "Select an available Proxmox host before checking networks.")
        if isinstance(plan, SdnPlan) and scope == "full":
            bundle_dir = os.getenv("RANGE42_BUNDLE_DIR")
            if not bundle_dir or not (Path(bundle_dir) / "proxmox/sdn_network.bootstrap/main.yml").is_file():
                raise NetworkBlocked("SDN_BUNDLE_UNAVAILABLE", "Install the SDN-enabled playbooks and controller, and set RANGE42_BUNDLE_DIR to their bundles directory. This scenario requires proxmox/sdn_network.bootstrap.")
        if client is None:
            async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned_client:
                return await check_scenario_networks(scenario_dir, host, scope=scope, client=owned_client)
        if isinstance(plan, SdnPlan):
            return [await _check_sdn(plan, host, scope, client)]
        interfaces = await _list(client, host, f"/nodes/{quote(host.node_name, safe='')}/network")
        for bridge in plan.bridges:
            if not any(row.get("iface") == bridge and row.get("active")
                       and row.get("type") in ("bridge", "OVSBridge") for row in interfaces):
                raise NetworkBlocked("BRIDGE_NOT_READY", f"Existing bridge {bridge} must be active on the selected target.")
        return [PreflightCheck(check="scenario_networks", result="pass", detail="Selected existing bridges are active.")]
    except NetworkBlocked as exc:
        return [exc.check]
    except ProxmoxReadError as exc:
        return [PreflightCheck(check="scenario_networks", result="block", code="SCENARIO_NETWORK_UNREADABLE", detail=str(exc))]
    except (ValueError, OSError, TypeError):
        # Validation exceptions may contain source data; never echo credentials or arbitrary manifest values.
        return [PreflightCheck(check="scenario_networks", result="block", code="SCENARIO_NETWORK_INVALID",
                               detail="Invalid network manifest or target network data. Check unique names (up to 8 alphanumeric characters), non-overlapping IPv4 subnets, usable gateways and explicit SNAT booleans.",
                               field_path="manifest/scenario_networks.json")]
