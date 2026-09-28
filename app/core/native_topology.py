"""Read scenario declarations and the NDJSON registry without running playbooks."""
from __future__ import annotations

from collections import Counter
import ipaddress
import json
from pathlib import Path

import yaml

LIMIT = 2 * 1024 * 1024


def read(path: Path, root: Path):
    if (not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
            or path.stat().st_size > LIMIT):
        raise ValueError("Scenario metadata is missing, too large or outside the repository")
    return path.read_text()


def reservations(root: Path, base: Path, manifest: dict) -> dict:
    ledger = root / "scenarios/_reserved.json"
    if not ledger.exists() and not ledger.is_symlink():
        return {"status": "missing", "issues": ["This repository has no scenarios/_reserved.json. Allocations have not been checked against a registry."]}
    try:
        rows = [json.loads(line) for line in read(ledger, root).splitlines() if line.strip()]
        if not rows or len(rows) > 32768 or any(not isinstance(row, dict)
                or type(row.get("vm_id")) is not int or not isinstance(row.get("scenario"), str) for row in rows):
            raise ValueError()
        expected = [{**row, "scenario": base.name} for row in manifest["vms"]]
        expected += [{**row, "scenario": base.name, "role": "template"} for row in manifest.get("templates", [])]
        def canonical(values):
            return Counter(json.dumps(row, sort_keys=True) for row in values)
        issues = []
        for vmid, count in Counter(vm["vm_id"] for vm in manifest["vms"]).items():
            if count > 1:
                issues.append(f"VMID {vmid} is declared more than once in this scenario.")
        if canonical(expected) != canonical([row for row in rows if row["scenario"] == base.name]):
            issues.append("The scenario manifest and scenarios/_reserved.json are out of sync. Regenerate the registry before deploying.")
        # Include all current manifests too, so a stale ledger cannot hide a newly
        # introduced collision. Shared template identity is explicitly permitted.
        paths = sorted((root / "scenarios").glob("*/manifest/scenario_vms.json"))
        if len(paths) > 1024:
            raise ValueError()
        total = 0
        for path in paths:
            data = read(path, root)
            total += len(data)
            if total > 16 * LIMIT:
                raise ValueError()
            doc = json.loads(data)
            rows.extend({**row, "scenario": path.parents[1].name} for row in doc["vms"])
            rows.extend({**row, "scenario": path.parents[1].name, "role": "template"} for row in doc.get("templates", []))
        for vm in expected:
            for other in rows:
                if vm == other:
                    continue
                shared = vm.get("role") == other.get("role") == "template" and vm["vm_id"] == other["vm_id"]
                if shared and all(vm.get(key) == other.get(key) for key in ("vm_name", "spec", "ip", "bridge")):
                    continue
                if vm["vm_id"] == other["vm_id"]:
                    issues.append(f"VMID {vm['vm_id']} conflicts with {other['scenario']} in the reservation registry or manifests.")
                if vm.get("bridge") and vm.get("ip") and (vm["bridge"], vm["ip"]) == (other.get("bridge"), other.get("ip")):
                    issues.append(f"Address {vm['ip']} on {vm['bridge']} conflicts with {other['scenario']}.")
        return {"status": "conflict" if issues else "checked", "issues": list(dict.fromkeys(issues))[:100]}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {"status": "invalid", "issues": ["Cannot validate scenarios/_reserved.json and the scenario manifests. Correct their contents before deploying."]}


def scenario_topology(root: Path, base: Path, manifest: dict) -> dict:
    keys = ("vm_id", "vm_name", "ip", "bridge", "role", "group", "spec")
    def public(rows):
        if not isinstance(rows, list) or len(rows) > 1024 or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid scenario resource declarations")
        return [{key: row[key] for key in keys if key in row and isinstance(row[key], (str, int))} for row in rows]
    vms, templates = public(manifest["vms"]), public(manifest.get("templates", []))
    networks = {row["bridge"]: {"vnet": row["bridge"]} for row in [*vms, *templates] if row.get("bridge")}
    warnings = ["The diagram shows declared resources. Feature choices and playbook conditions determine which resources are created."]
    # Existing scenarios declare literal SDN settings here. Dynamic expressions
    # remain unknown; never derive CIDRs or gateways from a network's name.
    bootstrap = base / "00_sdn_bootstrap/_main.yml"
    if bootstrap.exists():
        try:
            plays = yaml.safe_load(read(bootstrap, root))
            for play in plays:
                for task in play.get("tasks", []):
                    facts = task.get("ansible.builtin.set_fact", task.get("set_fact", {}))
                    for row in facts.get("_sdn_vnets", []):
                        if not isinstance(row, dict) or not isinstance(row.get("vnet"), str):
                            continue
                        subnet = str(ipaddress.ip_network(row["subnet"], strict=True))
                        network = {"vnet": row["vnet"], "subnet": subnet}
                        if row.get("gateway"):
                            network["gateway"] = str(ipaddress.ip_address(row["gateway"]))
                        if row["vnet"] in networks and networks[row["vnet"]].get("subnet", subnet) != subnet:
                            raise ValueError()
                        networks[row["vnet"]] = network
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
            networks = {row["bridge"]: {"vnet": row["bridge"]} for row in [*vms, *templates] if row.get("bridge")}
            warnings.append("Some network declarations are dynamic or invalid. Review the scenario files for their effective settings.")
    return {"vms": vms, "templates": templates, "networks": list(networks.values()),
            "warnings": warnings, "reservations": reservations(root, base, manifest)}
