"""Map native API chain data without exposing upstream response bodies."""
import asyncio
from datetime import datetime, timezone
from ipaddress import ip_network
import re
from urllib.parse import quote

from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data
from app.schemas.v1.runtime_reports import RuntimeReport


def _text(row, key):
    value = row.get(key)
    if value is not None and (not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid field")
    return value


def map_rules(rows):
    if len(rows) > 4096:
        raise ValueError("rule limit")
    result = []
    for row in rows:
        if (type(row.get("pos")) is not int or not 0 <= row["pos"] < 4096
                or row.get("type") not in ("in", "out", "forward", "group")
                or not isinstance(row.get("action"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["action"])
                or str(row.get("enable", 0)) not in ("0", "1")):
            raise ValueError("invalid rule")
        result.append({"position": row["pos"], "direction": row["type"], "action": row["action"],
                       "enabled": str(row.get("enable", 0)) == "1", **{
                           output: _text(row, source) for output, source in (
                               ("source", "source"), ("destination", "dest"), ("protocol", "proto"),
                               ("destination_port", "dport"), ("source_port", "sport"), ("interface", "iface"),
                               ("macro", "macro"), ("comment", "comment"), ("log", "log"))}})
    if len({row["position"] for row in result}) != len(result):
        raise ValueError("duplicate position")
    return sorted(result, key=lambda row: row["position"])


def map_aliases(rows):
    if len(rows) > 4096:
        raise ValueError("alias limit")
    result = []
    for row in rows:
        name = _text(row, "name")
        if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("invalid alias")
        result.append({"name": name, "cidr": str(ip_network(row["cidr"], strict=False)), "comment": _text(row, "comment")})
    if len({row["name"].lower() for row in result}) != len(result):
        raise ValueError("duplicate alias")
    return sorted(result, key=lambda row: row["name"])


async def read_chains(client, host, state):
    semaphore = asyncio.Semaphore(8)
    node = f"/nodes/{quote(host.node_name, safe='')}"

    async def chain(scope, base, vm_id=None):
        result = {"scope": scope, "vm_id": vm_id, "available": False, "rules": [], "aliases": []}
        try:
            async with semaphore:
                rules = map_rules(await list_proxmox_data(client, host, base + "/firewall/rules"))
                # Proxmox supports aliases at DC and guest scope, never node scope.
                aliases = [] if scope == "node" else map_aliases(await list_proxmox_data(client, host, base + "/firewall/aliases"))
            result.update(available=True, rules=rules, aliases=aliases)
        except (ProxmoxReadError, ValueError, TypeError, KeyError):
            result["error"] = "Cannot read this scope's complete rules and aliases. Check target permissions and configuration."
        return result

    return await asyncio.gather(chain("datacenter", "/cluster"), chain("node", node), *[
        chain("vm", f"{node}/qemu/{vm['vm_id']}", vm["vm_id"]) for vm in state["vms"] if vm["status"] == "owned"
    ])


async def report_from_state(client, host, state):
    chains = await read_chains(client, host, state)
    networks = [{**row, **({"active": None, "identity_matches": None} if state["sdn"]["errors"] else {})}
                for row in state["networks"]]
    cards = []
    for vm in state["vms"]:
        if vm["status"] != "owned":
            continue
        for nic in vm["nics"]:
            prerequisites = {"datacenter": state["firewall"]["datacenter_enabled"],
                             "guest": vm["firewall_enabled"], "nic": nic["firewall_enabled"]}
            flags = list(prerequisites.values())
            verdict = False if False in flags else None if None in flags else True
            cards.append({"vm_id": vm["vm_id"], "index": nic["index"], "bridge": nic["bridge"],
                          "filtering_configured": verdict,
                          "reasons": [f"{key}_{'unknown' if value is None else 'disabled'}" for key, value in prerequisites.items()
                                      if value is not True] or ["all_switches_enabled_traffic_unverified"]})
    return RuntimeReport(deployment_id=state["deployment_id"], target_host_id=host.id, node_name=host.node_name,
                         observed_at=datetime.now(timezone.utc), chains=chains, cards=cards,
                         switches=state["firewall"], sdn=state["sdn"], networks=networks,
                         partial=any(not row["available"] for row in chains) or bool(state["firewall"]["errors"])
                         or bool(state["sdn"]["errors"])
                         or any(vm["status"] in ("conflict", "unavailable") for vm in state["vms"])).model_dump(mode="json")
