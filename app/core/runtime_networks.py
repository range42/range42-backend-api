"""Reviewed lifecycle of one declared VNet; shared zones are never deleted."""
import asyncio
import hashlib
import json
from ipaddress import IPv4Network, ip_network
import re
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data as _list, read_proxmox_data as _read
from app.core.errors import Range42Error
from app.core.proxmox_tls import proxmox_verify
from app.core.runtime_operations import blocked
from app.core.runtime_state import runtime_targets
from app.core.scenario_networks import NetworkBlocked, _check_global_pending, _check_pending, _check_interface_overlap, _subnet_cidr


def network_marker(deployment_id):
    return f"range42-deployment-{deployment_id}"


class _GuardedReads:
    """Retain only hashes of static read data, never credentials or raw guest config."""
    def __init__(self, client):
        self.client = client
        self.guards = []

    async def get(self, url, **kwargs):
        response = await self.client.get(url, **kwargs)
        if response.status_code == 200:
            data = response.json()["data"]
            path = urlsplit(url).path.removeprefix("/api2/json")
            if kwargs.get("params"):
                path += "?" + urlencode(kwargs["params"])
            if path.startswith("/cluster/resources?"):
                self.guards.append({"path": path, "resources": sorted(f"{row['vmid']}:{row['node']}:{row['type']}" for row in data)})
            elif path == "/nodes":
                self.guards.append({"path": path, "nodes": sorted(row["node"] for row in data)})
            else:
                # PVE returns interface/SDN collections in hash iteration order.
                # Bind every row's contents, while ignoring that arbitrary order.
                resource = path.split("?", 1)[0]
                sort_key = ({"/cluster/sdn/zones": "zone", "/cluster/sdn/vnets": "vnet"}.get(resource)
                            or ("subnet" if re.fullmatch(r"/cluster/sdn/vnets/[^/]+/subnets", resource) else None)
                            or ("iface" if re.fullmatch(r"/nodes/[^/]+/network", resource) else None))
                if sort_key:
                    if not isinstance(data, list) or any(not isinstance(row, dict) or not isinstance(row.get(sort_key), str) for row in data):
                        raise ValueError("invalid network collection identity")
                    if len({row[sort_key] for row in data}) != len(data):
                        raise ValueError("duplicate network collection identity")
                    data = sorted(data, key=lambda row: row[sort_key])
                digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
                self.guards.append({"path": path, "digest": digest, **({"sort_key": sort_key} if sort_key else {})})
        return response


async def read_network_lifecycle(scenario_dir, host, *, deployment_id, client=None):
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await read_network_lifecycle(scenario_dir, host, deployment_id=deployment_id, client=owned)
    _, declaration = runtime_targets(scenario_dir)
    result = {"available": False, "error": None, "networks": [], "marker": network_marker(deployment_id)}
    if declaration is None:
        result["error"] = "This pinned scenario has no SDN declaration. Legacy bridges require an explicit migration outside deployment cleanup."
        return result
    client = _GuardedReads(client)
    try:
        permissions = await _read(client, host, "/access/permissions", params={"path": "/"})
        root = permissions.get("/", {}) if isinstance(permissions, dict) else {}
        if not all(root.get(key) == 1 for key in ("Sys.Audit", "VM.Audit", "SDN.Allocate")):
            raise blocked("Complete cluster audit and SDN allocation permissions are required; filtered inventory cannot authorize deletion")
        nodes = await _list(client, host, "/nodes")
        if len(nodes) != 1 or nodes[0].get("node") != host.node_name:
            raise blocked("Network lifecycle currently requires a single-node cluster. Shared apply on additional nodes needs their own native NAT preservation and readback.")
        zones = await _list(client, host, "/cluster/sdn/zones", params={"pending": 1})
        vnets = await _list(client, host, "/cluster/sdn/vnets", params={"pending": 1})
        if len(zones) > 512 or len(vnets) > 2048:
            raise ValueError("inventory bounds")
        _check_pending(zones + vnets)
        await _check_global_pending(host, client)
        interfaces = await _list(client, host, f"/nodes/{quote(host.node_name, safe='')}/network")
        subnets = {}
        for vnet in vnets:
            name = vnet["vnet"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,7}", name) or name in subnets:
                raise ValueError("invalid VNet")
            subnets[name] = await _list(client, host, f"/cluster/sdn/vnets/{name}/subnets", params={"pending": 1})
            if len(subnets[name]) > 256:
                raise ValueError("subnet bounds")
            _check_pending(subnets[name])
        interface_subnets = []
        for interface in interfaces:
            cidr = interface.get("cidr")
            if not cidr and interface.get("address") and interface.get("netmask"):
                cidr = f"{interface['address']}/{interface['netmask']}"
            if cidr:
                network = ip_network(cidr, strict=False)
                if network.version == 4:
                    interface_subnets.append(str(network))
        result["all_subnets"] = sorted(set(interface_subnets) | {
            str(_subnet_cidr(row)) for rows in subnets.values() for row in rows if _subnet_cidr(row).version == 4})
        resources = await _list(client, host, "/cluster/resources", params={"type": "vm"})
        if len(resources) > 2048:
            raise ValueError("guest bounds")
        semaphore = asyncio.Semaphore(8)

        async def guest(row):
            vmid, node, kind = row["vmid"], row["node"], row["type"]
            if (type(vmid) is not int or not 100 <= vmid <= 999999999 or kind not in ("qemu", "lxc")
                    or not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", node)):
                raise ValueError("guest identity")
            async with semaphore:
                config = await _read(client, host, f"/nodes/{node}/{kind}/{vmid}/config")
            if not isinstance(config, dict):
                raise ValueError("guest config")
            bridges = []
            for key, value in config.items():
                if not re.fullmatch(r"net[0-9]+", key):
                    continue
                if not isinstance(value, str):
                    raise ValueError("guest NIC")
                fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
                if "bridge" in fields:
                    bridges.append(fields["bridge"])
            return {"vm_id": vmid, "node": node, "type": kind, "template": str(config.get("template", row.get("template", 0))) == "1",
                    "bridges": sorted(bridges)}

        guests = await asyncio.gather(*(guest(row) for row in resources))
        if len({row["vm_id"] for row in guests}) != len(guests):
            raise ValueError("duplicate guest")
        zone = next((row for row in zones if row.get("zone") == declaration.zone), None)
        if zone:
            members = zone.get("nodes") or []
            members = members.split(",") if isinstance(members, str) else members
            if zone.get("type") != "simple" or (members and host.node_name not in members):
                raise blocked("The declared zone has another type or does not include the selected node")
        result["zone_exists"] = zone is not None
        for desired in declaration.vnets:
            live = next((row for row in vnets if row["vnet"] == desired.vnet), None)
            rows = subnets.get(desired.vnet, [])
            exact = [row for row in rows if str(_subnet_cidr(row)) == desired.subnet]
            subnet = exact[0] if len(exact) == 1 else {}
            errors = []
            try:
                _check_interface_overlap(desired, interfaces)
                if not live and any(row.get("iface") == desired.vnet for row in interfaces):
                    raise blocked("A legacy interface or bridge already uses this VNet name. Review migration before creating SDN.")
                for name, candidates in subnets.items():
                    for candidate in candidates:
                        network = _subnet_cidr(candidate)
                        if network.version == 4 and network.overlaps(IPv4Network(desired.subnet)) and (
                                name != desired.vnet or str(network) != desired.subnet):
                            raise blocked("The declared subnet overlaps another network")
            except (NetworkBlocked, Range42Error) as exc:
                if isinstance(exc, NetworkBlocked):
                    errors.append(exc.check.detail)
                else:
                    errors.append(exc.message)
            result["networks"].append({**desired.model_dump(), "zone": declaration.zone, "exists": live is not None,
                "owned": bool(live and live.get("alias") == result["marker"]), "subnet_id": subnet.get("subnet"),
                "identity_matches": bool(zone and live and live.get("zone") == declaration.zone and len(rows) == 1 and subnet
                                         and subnet.get("gateway") == desired.gateway),
                "configured_snat": str(subnet.get("snat", 0)) == "1" if subnet else None,
                "attachments": [{key: row[key] for key in ("vm_id", "node", "type", "template")} for row in guests if desired.vnet in row["bridges"]],
                "errors": errors})
        result["available"] = True
        result["guards"] = sorted(client.guards, key=lambda guard: guard["path"])
    except (ProxmoxReadError, ValueError, TypeError, KeyError, NetworkBlocked) as exc:
        result["error"] = exc.check.detail if isinstance(exc, NetworkBlocked) else "Cannot verify the complete cluster network and guest inventory"
    except Range42Error as exc:
        result["error"] = exc.message
    return result


def network_plan(request, observation):
    if not observation.get("available"):
        raise blocked(observation.get("error") or "Network lifecycle observations are unavailable", "SDN_LIFECYCLE_UNAVAILABLE")
    network = next((row for row in observation["networks"] if row["vnet"] == request["vnet"]), None)
    if network is None:
        raise blocked("Select a VNet from this deployment's pinned declaration")
    if network["errors"]:
        raise blocked(network["errors"][0])
    if network["attachments"]:
        raise blocked("This VNet still has attached guests or templates. Detach them explicitly before changing its lifecycle.", "SDN_NETWORK_ATTACHED")
    variables = {"BUNDLE_SDN_ZONE": network["zone"], "BUNDLE_SDN_VNET": network["vnet"],
                 "BUNDLE_SDN_SUBNET_CIDR": network["subnet"],
                 "BUNDLE_SDN_SUBNET_SNAT": int(network["snat"])}
    if network["gateway"] is not None:
        variables["BUNDLE_SDN_SUBNET_GATEWAY"] = network["gateway"]
    steps = []
    def step(name, values=None):
        steps.append({"bundle": f"proxmox/sdn_network.{name}", "variables": values or {}})
    if request["action"] == "create":
        if network["exists"]:
            raise blocked("This VNet already exists. Creation never adopts or replaces an existing network.")
        if not observation["zone_exists"]:
            step("create.sdn_zone", {"BUNDLE_SDN_ZONE": network["zone"]})
        step("create.sdn_vnet", {**variables, "sdn_vnet_alias": observation["marker"]})
        step("create.sdn_subnet", variables)
    elif request["action"] == "delete":
        if not network["owned"] or not network["identity_matches"] or not isinstance(network["subnet_id"], str):
            raise blocked("Deletion requires this deployment's exact VNet ownership marker, zone, single subnet and gateway", "SDN_NETWORK_OWNERSHIP")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", network["subnet_id"]):
            raise blocked("The live subnet identity is invalid")
        step("delete.sdn_subnet", {"BUNDLE_SDN_VNET": network["vnet"], "BUNDLE_SDN_SUBNET_ID": network["subnet_id"]})
        step("delete.sdn_vnet", {"BUNDLE_SDN_VNET": network["vnet"]})
    else:
        raise blocked("Choose create or delete; unrelated pending changes cannot be applied by this deployment")
    step("apply")
    step("reconcile.snat_rules", {"BUNDLE_SDN_SUBNET_CIDR": network["subnet"], "BUNDLE_SDN_SNAT_WANT": int(network["snat"]) if request["action"] == "create" else 0})
    return {"vmids": [], "missing_vmids": [], "variables": {}, "steps": steps, "network": network,
            "attachments": network["attachments"], "preserve_zone": True, "all_subnets": observation["all_subnets"],
            "shared_scope": "cluster_sdn_apply", "ownership_marker": observation["marker"], "guards": observation["guards"]}


def lifecycle_guard_play(plan):
    return {"name": "Recheck reviewed cluster state before network lifecycle changes", "hosts": "proxmox", "gather_facts": False,
            "tasks": [{"name": "Read and verify every reviewed network and guest identity", "range42_network_guard": {
                "api_host": "{{ proxmox_api_host }}",
                "authorization": "PVEAPIToken={{ proxmox_api_user }}!{{ proxmox_api_token_id }}={{ proxmox_api_token_secret }}",
                "ca_path": "{{ lookup('env', 'RANGE42_PROXMOX_CA_FILE') | default(omit, true) }}",
                "guards": plan["guards"],
            }, "no_log": True}]}


def preserve_nat_plays(plan):
    """Use native readers/deletion primitives to preserve zero-count neighbours too."""
    role = {"ansible.builtin.include_role": {"name": "range42-ansible_roles-proxmox_controller"}}
    before = {"name": "Record native NAT counts before network lifecycle changes", "hosts": "proxmox", "gather_facts": False,
              "vars": {"r42_lifecycle_subnets": plan["all_subnets"]}, "tasks": [
                  {"ansible.builtin.set_fact": {"r42_lifecycle_raw_shapes": "{{ r42_native_nat_before.stdout_lines | select('match', '^-A POSTROUTING -s ') | unique | list }}"}},
                  {"ansible.builtin.assert": {"that": [
                      r"(r42_lifecycle_raw_shapes | length) == (r42_lifecycle_raw_shapes | map('regex_findall', '^-A POSTROUTING -s (\S+) ') | map('first') | unique | length)",
                  ], "fail_msg": "One source has different raw NAT rules, including destination addresses. Review those rules before applying SDN."}},
                  {**role, "vars": {"proxmox_vm_action": "network_list_snat_rules"}},
                  {"ansible.builtin.assert": {"that": [
                      "(network_list_snat_rules | map(attribute='snat_source') | list | length) == (network_list_snat_rules | map(attribute='snat_source') | unique | length)",
                  ], "fail_msg": "Network lifecycle requires one native NAT rule shape per source. Review mixed source rules first."}},
                  {"ansible.builtin.set_fact": {"r42_lifecycle_nat_before": {}}},
                  {"ansible.builtin.set_fact": {"r42_lifecycle_nat_before": "{{ r42_lifecycle_nat_before | combine({ r42_lifecycle_source: (network_list_snat_rules | selectattr('snat_source', 'equalto', r42_lifecycle_source) | map(attribute='snat_count') | sum) }) }}"},
                   "loop": "{{ (r42_lifecycle_subnets + (network_list_snat_rules | map(attribute='snat_source') | list)) | unique | list }}",
                   "loop_control": {"loop_var": "r42_lifecycle_source"}},
                  {"ansible.builtin.debug": {"var": "r42_lifecycle_nat_before"}},
              ]}
    after = {"name": "Preserve unrelated native NAT counts after the shared apply", "hosts": "proxmox", "gather_facts": False,
             "vars": {"r42_lifecycle_selected_subnet": plan["network"]["subnet"]}, "tasks": [
                 {**role, "vars": {"proxmox_vm_action": "network_delete_extra_snat_rules",
                                   "sdn_subnet_cidr": "{{ r42_lifecycle_pair.key }}", "sdn_snat_want": "{{ r42_lifecycle_pair.value | int }}"},
                  "loop": "{{ r42_lifecycle_nat_before | dict2items }}", "loop_control": {"loop_var": "r42_lifecycle_pair"},
                  "when": "r42_lifecycle_pair.key != r42_lifecycle_selected_subnet"},
             ]}
    return before, after
