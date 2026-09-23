"""Read configured guest/node addresses and globally assert candidate VMIDs.

Proxmox nextid is an availability assertion at the time of the GET, not a lock.
It sees VMIDs hidden from the permission-filtered cluster/resources endpoint.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass, field

import httpx

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data, read_proxmox_data


@dataclass
class Occupancy:
    vmids: set[int] = field(default_factory=set)
    addresses: set[tuple[str, str]] = field(default_factory=set)
    unknown_address_nics: int = 0


def unavailable(message: str) -> Range42Error:
    return Range42Error(code="ALLOCATION_OCCUPANCY_UNAVAILABLE", status=409, message=message)


def _properties(value: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in value.split(",") if "=" in part)


def _record_address(result: Occupancy, bridge: str, value: str | None):
    if not value or value in {"dhcp", "manual", "auto"}:
        result.unknown_address_nics += 1
        return
    try:
        address = ipaddress.ip_interface(value)
    except ValueError as exc:
        raise unavailable("A guest or host has an unparseable configured address; correct its network configuration before reserving addresses.") from exc
    if address.version == 4:
        result.addresses.add((bridge, str(address.ip)))


async def read_occupancy(client: httpx.AsyncClient, host: ProxmoxHost) -> Occupancy:
    result = Occupancy()
    try:
        permissions = await read_proxmox_data(client, host, "/access/permissions", params={"path": "/vms"})
        if not isinstance(permissions, dict) or not isinstance(permissions.get("/vms"), dict) or permissions["/vms"].get("VM.Audit") != 1:
            raise unavailable("Address allocation requires propagated VM.Audit on /vms so configured guest addresses can be inspected.")
        rows = await list_proxmox_data(client, host, "/cluster/resources", params={"type": "vm"})
        nodes = await list_proxmox_data(client, host, "/nodes")
        if len(rows) > 4096 or len(nodes) > 128:
            raise unavailable("The cluster exceeds the bounded allocation audit size.")
        if not any(node.get("node") == host.node_name and node.get("status") == "online" for node in nodes):
            raise unavailable("The selected target node is not online in this cluster.")
        semaphore = asyncio.Semaphore(8)

        async def inspect_guest(row: dict):
            vmid, kind, node = row.get("vmid"), row.get("type"), row.get("node")
            if type(vmid) is not int or vmid < 100 or kind not in {"qemu", "lxc"} or not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", node):
                raise unavailable("Proxmox returned an incomplete guest inventory.")
            result.vmids.add(vmid)
            # A pending network edit can differ from the running configuration.
            # Reserve both views until the operator applies or reverts the edit.
            for current in (0, 1):
                async with semaphore:
                    config = await read_proxmox_data(client, host, f"/nodes/{node}/{kind}/{vmid}/config", params={"current": current})
                if not isinstance(config, dict):
                    raise unavailable("Proxmox returned an incomplete guest configuration.")
                for key, value in config.items():
                    if not re.fullmatch(r"net\d+", key):
                        continue
                    if not isinstance(value, str):
                        raise unavailable("Proxmox returned an invalid guest NIC configuration.")
                    nic = _properties(value)
                    bridge = nic.get("bridge")
                    if not bridge:
                        continue
                    ipconfig = config.get(f"ipconfig{key[3:]}", "") if kind == "qemu" else value
                    if not isinstance(ipconfig, str):
                        raise unavailable("Proxmox returned an invalid guest address configuration.")
                    _record_address(result, bridge, _properties(ipconfig).get("ip"))

        async def inspect_node(row: dict):
            node = row.get("node")
            if not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", node):
                raise unavailable("Proxmox returned an incomplete node inventory.")
            async with semaphore:
                networks = await list_proxmox_data(client, host, f"/nodes/{node}/network")
            for network in networks:
                value = network.get("cidr") or network.get("address")
                if value:
                    if not isinstance(network.get("iface"), str) or not isinstance(value, str):
                        raise unavailable("Proxmox returned an invalid host network configuration.")
                    _record_address(result, network["iface"], value)
        await asyncio.gather(*(inspect_guest(row) for row in rows), *(inspect_node(node) for node in nodes))
        return result
    except ProxmoxReadError as exc:
        raise unavailable(str(exc)) from exc


async def vmid_is_free(client: httpx.AsyncClient, host: ProxmoxHost, vmid: int) -> bool:
    try:
        response = await client.get(
            f"{host.api_url.rstrip('/')}/api2/json/cluster/nextid",
            params={"vmid": vmid}, headers={"Authorization": f"PVEAPIToken={host.token_ref}"},
        )
        data = response.json()
        # Only the exact public API conflict is occupancy; permission failures,
        # malformed bodies, and unrelated HTTP 400 responses must fail closed.
        if response.status_code == 400 and isinstance(data, dict) and data.get("errors", {}).get("vmid") == f"VM {vmid} already exists":
            return False
        response.raise_for_status()
        if not isinstance(data, dict) or type(data.get("data")) not in (str, int) or str(data["data"]) != str(vmid):
            raise ValueError("VMID availability assertion did not match")
        return True
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise unavailable("Cannot confirm cluster-wide VMID availability. Check Proxmox connectivity and token permissions.") from exc
