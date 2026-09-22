"""Native Proxmox policy adapters with ownership, reference and concurrency checks."""
from copy import deepcopy
from ipaddress import ip_network
import re
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from app.core.errors import Range42Error
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data as _list, read_proxmox_data as _read
from app.core.proxmox_tls import proxmox_verify
from app.core.runtime_networks import _GuardedReads
from app.core.runtime_operations import blocked
from app.core.runtime_reports import map_aliases, map_rules
from app.core.runtime_state import _guests, runtime_targets
from app.schemas.v1.runtime_firewall import FirewallAlias, FirewallRule


def _digest(rows):
    values = {row.get("digest") for row in rows}
    if len(values) != 1 or not isinstance(next(iter(values)), str) or not re.fullmatch(r"[a-f0-9]{40,64}", next(iter(values))):
        raise blocked("The native configuration digest is unavailable; refresh this scope before editing")
    return next(iter(values))


def _management(rule, scope):
    if rule["direction"] != "in":
        # Removing an outbound allow can cut off management replies to client
        # ephemeral ports even when the inbound SSH rule is untouched.
        return True
    if rule.get("protocol") == "udp":
        return False
    ports = rule.get("destination_port")
    if rule.get("macro") or not ports or not re.fullmatch(r"[0-9:,]+", ports):
        return True
    protected = (22,) if scope == "vm" else (22, 8006)
    for item in ports.split(","):
        bounds = [int(value) for value in item.split(":")]
        if any(bounds[0] <= port <= bounds[-1] for port in protected):
            return True
    return False


def _validate_rule(rule, scope, aliases, dc_aliases):
    if rule["action"] != "ACCEPT" and (_management(rule, scope) or scope != "vm"):
        raise blocked("This policy could block management access. Host/DC deny policies and management-port denies require an explicit console workflow.", "FIREWALL_MANAGEMENT_PROTECTED")
    local_names = {row["name"].lower() for row in aliases}
    dc_names = {row["name"].lower() for row in dc_aliases}
    for value in (rule.get("source"), rule.get("destination")):
        if not value:
            continue
        try:
            ip_network(value)
        except ValueError:
            name = value.removeprefix("dc/").lower()
            valid = name in dc_names if value.startswith("dc/") else name in local_names | dc_names
            if not valid:
                raise blocked("A rule references an alias that is absent from the selected firewall scope", "FIREWALL_ALIAS_MISSING")


async def _reference_rows(client, host, base):
    rules = await _list(client, host, base + "/rules")
    ipsets = await _list(client, host, base + "/ipset")
    if len(ipsets) > 256 or len(rules) > 4096:
        raise ValueError("reference bounds")
    for row in ipsets:
        if not isinstance(row.get("name"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", row["name"]):
            raise ValueError("invalid IP set")
        entries = await _list(client, host, f"{base}/ipset/{row['name']}")
        if len(entries) > 4096:
            raise ValueError("reference bounds")
        rules.extend(entries)
    return rules


async def _alias_references(client, host, base, scope, name):
    rows = await _reference_rows(client, host, base)
    if scope == "datacenter":
        permissions = await _read(client, host, "/access/permissions", params={"path": "/"})
        if not isinstance(permissions, dict) or not all(permissions.get("/", {}).get(key) == 1 for key in ("Sys.Audit", "VM.Audit")):
            raise blocked("Datacenter alias changes require complete cluster audit permissions to check references")
        nodes = await _list(client, host, "/nodes")
        guests = await _list(client, host, "/cluster/resources", params={"type": "vm"})
        groups = await _list(client, host, "/cluster/firewall/groups")
        if len(nodes) > 512 or len(guests) > 2048 or len(groups) > 512:
            raise ValueError("scope bounds")
        for row in nodes:
            node = quote(row["node"], safe="")
            rows.extend(await _list(client, host, f"/nodes/{node}/firewall/rules"))
        for row in guests:
            if row["type"] not in ("qemu", "lxc") or type(row["vmid"]) is not int:
                raise ValueError("guest identity")
            rows.extend(await _reference_rows(client, host, f"/nodes/{quote(row['node'], safe='')}/{row['type']}/{row['vmid']}/firewall"))
        for row in groups:
            rows.extend(await _list(client, host, f"/cluster/firewall/groups/{quote(row['group'], safe='')}"))
    tokens = {name.lower(), "dc/" + name.lower()}
    if any(tokens & set(re.split(r"[\s,;!]+", str(row.get(field, "")).lower())) for row in rows for field in ("source", "dest", "cidr")):
        raise blocked("This alias is referenced by a rule or IP set. Update those references explicitly before rename or deletion.", "FIREWALL_ALIAS_REFERENCED")


async def firewall_plan(scenario_dir, host, *, deployment_id, request, client=None):
    operation = TypeAdapter(FirewallAlias if request["kind"] == "firewall_alias" else FirewallRule).validate_python(request)
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await firewall_plan(scenario_dir, host, deployment_id=deployment_id, request=request, client=owned)
    client = _GuardedReads(client)
    marker = f"range42-deployment:{deployment_id}"
    node = f"/nodes/{quote(host.node_name, safe='')}"
    base = "/cluster/firewall" if operation.scope == "datacenter" else node + "/firewall"
    vmids = []
    try:
        if operation.scope == "vm":
            vms, _ = runtime_targets(scenario_dir)
            selected = [vm for vm in vms if vm["vm_id"] == operation.vm_id]
            if not selected or (await _guests(client, host, selected, deployment_id, None))[0]["status"] != "owned":
                raise blocked("Select an exactly owned guest from this deployment", "FIREWALL_GUEST_OWNERSHIP")
            vmids = [operation.vm_id]
            base = f"{node}/qemu/{operation.vm_id}/firewall"
        raw_rules = await _list(client, host, base + "/rules")
        rules = map_rules(raw_rules)
        raw_aliases = [] if operation.scope == "node" else await _list(client, host, base + "/aliases")
        aliases = map_aliases(raw_aliases)
        dc_aliases = aliases if operation.scope == "datacenter" else map_aliases(await _list(client, host, "/cluster/firewall/aliases"))
        expected_rules, expected_aliases = deepcopy(rules), deepcopy(aliases)
        if operation.kind == "firewall_alias":
            current = next((row for row in aliases if row["name"].lower() == operation.name.lower()), None)
            if operation.action == "create":
                if current or any(row["name"].lower() == operation.name.lower() for row in dc_aliases):
                    raise blocked("An alias with this name already exists; creation never replaces or shadows an alias")
                body = {"name": operation.name, "cidr": operation.cidr, "comment": marker}
                change = {"method": "POST", "path": base + "/aliases", "body": body}
                expected_aliases.append(body)
            else:
                if not current or current.get("comment") != marker:
                    raise blocked("Alias mutation requires this deployment's exact ownership marker", "FIREWALL_ALIAS_OWNERSHIP")
                digest = _digest(raw_aliases)
                await _alias_references(client, host, base, operation.scope, current["name"])
                path = base + "/aliases/" + quote(current["name"], safe="")
                expected_aliases.remove(current)
                if operation.action == "delete":
                    change = {"method": "DELETE", "path": path, "body": {"digest": digest}}
                else:
                    if any(row["name"].lower() == operation.new_name.lower() for row in [*aliases, *dc_aliases]):
                        raise blocked("The new alias name already exists in this scope")
                    change = {"method": "PUT", "path": path, "body": {"rename": operation.new_name, "cidr": current["cidr"], "comment": marker, "digest": digest}}
                    expected_aliases.append({**current, "name": operation.new_name})
        else:
            if [row["position"] for row in rules] != list(range(len(rules))):
                raise blocked("The native rule chain has missing positions; refresh before editing")
            current = rules[operation.position] if operation.position is not None and operation.position < len(rules) else None
            if operation.action != "create":
                if (not current or not re.fullmatch(re.escape(marker) + r";rule:[A-Za-z][A-Za-z0-9_-]{0,63}", current.get("comment") or "")
                        or _management(current, operation.scope)):
                    raise blocked("Only deployment-owned non-management rules may be edited, moved or deleted", "FIREWALL_RULE_PROTECTED")
            if operation.rule:
                rule = operation.rule.model_dump()
                _validate_rule(rule, operation.scope, aliases, dc_aliases)
                comment = marker + ";rule:" + operation.name if operation.action == "create" else current["comment"]
                if operation.action == "create" and any(row.get("comment") == comment for row in rules):
                    raise blocked("This policy rule name already exists")
                body = {"type": rule["direction"], "action": rule["action"], "proto": rule["protocol"], "dport": rule["destination_port"],
                        "enable": int(rule["enabled"]), "comment": comment}
                body.update({key: value for key, value in (("source", rule["source"]), ("dest", rule["destination"])) if value is not None})
                mapped = map_rules([{**body, "pos": operation.position or 0}])[0]
                if operation.action == "create":
                    change = {"method": "POST", "path": base + "/rules", "body": body}
                    expected_rules.insert(0, mapped)
                else:
                    # Full replacement of this supported rule's optional fields.
                    body["delete"] = ",".join(key for key in ("source", "dest", "sport", "iface", "macro", "log", "icmp-type") if key not in body)
                    change = {"method": "PUT", "path": base + f"/rules/{operation.position}", "body": {**body, "digest": _digest(raw_rules)}}
                    expected_rules[operation.position] = mapped
            elif operation.action == "delete":
                change = {"method": "DELETE", "path": base + f"/rules/{operation.position}", "body": {"digest": _digest(raw_rules)}}
                expected_rules.pop(operation.position)
            else:
                if operation.move_to >= len(rules):
                    raise blocked("The destination rule position does not exist")
                insertion = operation.move_to + int(operation.position < operation.move_to)
                change = {"method": "PUT", "path": base + f"/rules/{operation.position}", "body": {"moveto": insertion, "digest": _digest(raw_rules)}}
                expected_rules.insert(operation.move_to, expected_rules.pop(operation.position))
            for position, row in enumerate(expected_rules):
                row["position"] = position
        return {"vmids": vmids, "missing_vmids": [], "variables": {}, "api_change": change,
                "scope": operation.scope, "scope_path": base, "before_rules": rules, "before_aliases": aliases,
                "expected_rules": expected_rules, "expected_aliases": sorted(expected_aliases, key=lambda row: row["name"]),
                "guards": sorted(client.guards, key=lambda row: row["path"]), "shared_scope": operation.scope}
    except Range42Error:
        raise
    except (ProxmoxReadError, ValueError, KeyError, TypeError):
        raise blocked("Cannot read the complete firewall scope and references. Check target permissions before editing.", "FIREWALL_SCOPE_UNAVAILABLE") from None


def firewall_change_play(plan):
    change = plan["api_change"]
    parameters = {"url": "https://{{ proxmox_api_host }}/api2/json" + change["path"], "method": change["method"],
                  "headers": {"Authorization": "PVEAPIToken={{ proxmox_api_user }}!{{ proxmox_api_token_id }}={{ proxmox_api_token_secret }}"},
                  "validate_certs": True, "ca_path": "{{ lookup('env', 'RANGE42_PROXMOX_CA_FILE') | default(omit, true) }}"}
    if change["method"] == "DELETE":
        parameters["url"] += "?digest=" + change["body"]["digest"]
    else:
        parameters.update(body_format="json", body=change["body"])
    return {"name": "Apply the reviewed native Proxmox firewall change", "hosts": "proxmox", "gather_facts": False,
            "tasks": [{"name": "Change only the reviewed scope with native concurrency checks", "ansible.builtin.uri": parameters, "no_log": True}]}


async def read_firewall_control(scenario_dir, host, *, deployment_id, request, client=None):
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await read_firewall_control(scenario_dir, host, deployment_id=deployment_id, request=request, client=owned)
    result = {"available": False, "rules": [], "aliases": []}
    try:
        node = f"/nodes/{quote(host.node_name, safe='')}"
        base = "/cluster/firewall" if request["scope"] == "datacenter" else node + "/firewall"
        if request["scope"] == "vm":
            vms, _ = runtime_targets(scenario_dir)
            selected = [vm for vm in vms if vm["vm_id"] == request["vm_id"]]
            if not selected or (await _guests(client, host, selected, deployment_id, None))[0]["status"] != "owned":
                return result
            base = f"{node}/qemu/{request['vm_id']}/firewall"
        rules = map_rules(await _list(client, host, base + "/rules"))
        aliases = [] if request["scope"] == "node" else map_aliases(await _list(client, host, base + "/aliases"))
        result.update(available=True, rules=rules, aliases=aliases)
    except (ProxmoxReadError, ValueError, KeyError, TypeError, Range42Error):
        pass
    return result
