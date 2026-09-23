"""Persist observed runtime results before closing normal or recovered jobs."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import yaml

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.events import EventsReader
from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.runtime_state import read_runtime_state
from app.core.runtime_operations import target_identity


def assess_runtime_result(request: dict, plan: dict, state: dict, events) -> dict:
    result = {"observed": state, "desired_reached": False, "partial": False}
    if request["kind"] in ("firewall_alias", "firewall_rule"):
        control = state.get("firewall_control", {})
        result.update(desired_reached=bool(control.get("available") and control["rules"] == plan["expected_rules"]
                                          and control["aliases"] == plan["expected_aliases"]), live_forwarding_verified=False)
        return result
    if request["kind"] == "sdn_network":
        from app.core.native_sdn import native_snat_count
        sample = None
        before = None
        for event in events:
            response = event.get("payload", {}).get("res", {})
            if "r42_lifecycle_nat_before" in response:
                before = response["r42_lifecycle_nat_before"]
            if "r42_native_snat_observation" in response:
                sample = response["r42_native_snat_observation"]
        count = native_snat_count(sample, plan["network"]["subnet"], state.get("node_name"))
        preserved = None
        if isinstance(before, dict) and len(before) <= 4096 and all(isinstance(key, str) and type(value) is int and 0 <= value < 2**31 for key, value in before.items()):
            counts = [(value, native_snat_count(sample, key, state.get("node_name"))) for key, value in before.items() if key != plan["network"]["subnet"]]
            if count is not None and all(observed is not None for _, observed in counts):
                preserved = all(wanted == observed for wanted, observed in counts)
        lifecycle = state.get("network_lifecycle", {})
        network = next((row for row in lifecycle.get("networks", []) if row["vnet"] == request["vnet"]), {})
        running = next((row for row in state["networks"] if row["vnet"] == request["vnet"]), {})
        reached = False
        if lifecycle.get("available") and state["sdn"]["pending_changes"] is False and not state["sdn"]["errors"]:
            if request["action"] == "delete":
                reached = network.get("exists") is False and running.get("active") is False and count == 0
            else:
                reached = bool(network.get("owned") and network.get("identity_matches") and running.get("active")
                               and network.get("configured_snat") is plan["network"]["snat"] and count == int(plan["network"]["snat"]))
        result.update(desired_reached=reached and preserved is True, partial=reached and preserved is not True,
                      live_snat_rule_count=count, unrelated_nat_preserved=preserved, live_forwarding_verified=False,
                      recovery="Refresh the network plan and inspect Proxmox pending changes. A failed or partial operation is never retried or rolled back automatically.")
        return result
    if request["kind"] == "runtime_observe":
        from app.core.native_sdn import native_snat_count
        from app.schemas.v1.runtime_reports import LiveNatReport, NativeNatRule
        sample = None
        for event in events:
            response = event.get("payload", {}).get("res", {})
            if "r42_native_snat_observation" in response:
                sample = response["r42_native_snat_observation"]
        valid = plan.get("contract") == "native-sdn-20260921" and native_snat_count(sample, "", state.get("node_name")) is not None
        subnets = {row["subnet"] for row in state["networks"]}
        report = LiveNatReport()
        if valid:
            report = LiveNatReport(available=True, observed_at=datetime.now(timezone.utc), reason=None,
                                  rules=[NativeNatRule.model_validate(row) for row in sample["rules"] if row["snat_source"] in subnets])
        result.update(desired_reached=valid, live_nat=report.model_dump(mode="json"), live_forwarding_verified=False)
        return result
    if request["kind"] == "host_firewall":
        switches = [state.get("firewall", {}).get(key) for key in ("datacenter_enabled", "node_enabled")]
        matches = [value is request["enabled"] for value in switches]
        result.update(desired_reached=all(matches) and not state["firewall"].get("errors"),
                      partial=any(matches) and not all(matches), live_forwarding_verified=False)
        return result
    if request["kind"] == "sdn_snat":
        network = next((network for network in state["networks"] if network["vnet"] == request["vnet"]), {})
        count = None
        for event in events:
            response = event.get("payload", {}).get("res", {})
            if plan.get("contract") == "native-sdn-20260921":
                from app.core.native_sdn import native_snat_count
                if "r42_native_snat_observation" in response:
                    count = native_snat_count(response["r42_native_snat_observation"], plan["subnet"], state.get("node_name"))
                continue
            sample = response.get("network_delete_extra_snat_rules") or response.get("ansible_facts", {}).get("network_delete_extra_snat_rules")
            if (isinstance(sample, dict) and sample.get("subnet_cidr") == plan["subnet"]
                    and sample.get("snat_host") == "r42-proxmox-cli"
                    and sample.get("snat_rule_matching") == "exact_source_nat_target_v1"
                    and sample.get("proxmox_node") == state.get("node_name")
                    and type(sample.get("snat_want")) is int and sample["snat_want"] == int(request["enabled"])
                    and type(sample.get("snat_after")) is int and 0 <= sample["snat_after"] < 2**31):
                count = sample["snat_after"]
        result.update(live_snat_rule_count=count, live_forwarding_verified=False)
        result["desired_reached"] = bool(state.get("sdn", {}).get("pending_changes") is False
                                          and state.get("sdn", {}).get("errors") == []
                                          and network.get("identity_matches") and network.get("active")
                                          and network.get("configured_snat") is request["enabled"]
                                          and count == int(request["enabled"]))
        return result
    matched = []
    for vmid in plan["vmids"]:
        vm = next((vm for vm in state["vms"] if vm["vm_id"] == vmid), {})
        if (vm.get("status") == "owned" and vm.get("firewall_enabled") is request["enabled"]
                and (vm.get("nics") or not request["enabled"])
                and all(nic["firewall_enabled"] is request["enabled"] for nic in vm.get("nics", []))):
            matched.append(vmid)
    unmatched = [vmid for vmid in plan["vmids"] if vmid not in matched]
    result.update(matched_vmids=matched, mismatched_vmids=unmatched, missing_vmids=plan["missing_vmids"],
                  desired_reached=bool(matched) and not unmatched and not plan["missing_vmids"],
                  partial=bool(matched) and bool(unmatched or plan["missing_vmids"]))
    return result


async def observe_runtime_completion(attempt_id: str, writer) -> dict:
    """Return finish_attempt options. Readback errors never become a green job."""
    async with get_session_factory()() as session:
        attempt = await session.get(Attempt, attempt_id)
        if attempt is None or attempt.scope != "runtime":
            return {}
        deployment = await session.get(Deployment, attempt.deployment_id)
        host = await session.get(ProxmoxHost, deployment.target_host_id)
        result = {"desired_reached": False, "partial": False, "error": "Runtime readback is unavailable"}
        try:
            if not attempt.operation or attempt.operation.get("target_identity") != target_identity(host):
                result["error"] = "The target API address or node changed; runtime completion cannot be verified against another host"
                raise ValueError("runtime target changed")
            artifact = Path(deployment.workspace_path) / "runner" / attempt.id
            path = artifact / "runtime/context.yml"
            if path.is_symlink() or path.stat().st_size > 1048576:
                raise ValueError("invalid runtime context")
            context = yaml.safe_load(path.read_text())
            scenario = (artifact / context["scenario_dir"]).resolve()
            if not scenario.is_relative_to((artifact / "checkout").resolve()):
                raise ValueError("invalid scenario context")
            observed = await read_runtime_state(scenario, host, deployment_id=deployment.id)
            if attempt.operation["request"]["kind"] == "sdn_network":
                from app.core.runtime_networks import read_network_lifecycle
                observed["network_lifecycle"] = await read_network_lifecycle(scenario, host, deployment_id=deployment.id)
            if attempt.operation["request"]["kind"] in ("firewall_alias", "firewall_rule"):
                from app.core.runtime_firewall import read_firewall_control
                observed["firewall_control"] = await read_firewall_control(scenario, host, deployment_id=deployment.id, request=attempt.operation["request"])
            events = (event for event in EventsReader(writer.path).read_range() if event.get("attempt_id") == attempt_id)
            result = assess_runtime_result(attempt.operation["request"], context["plan"], observed, events)
        except (OSError, ValueError, KeyError, TypeError, Range42Error, yaml.YAMLError):
            pass
        attempt.operation_result = result
        await session.commit()
        writer.append({"event_type": "log_line", "payload": {"text": "Runtime state readback", "runtime_result": result}},
                      attempt_id=attempt_id, deployment_id=deployment.id)
    return {"partial": result["partial"],
            "error_code": None if result["desired_reached"] else "RUNTIME_STATE_MISMATCH"}
