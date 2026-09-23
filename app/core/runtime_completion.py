"""Persist observed runtime results before closing normal or recovered jobs."""
from __future__ import annotations

from pathlib import Path

import yaml

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.events import EventsReader
from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.runtime_state import read_runtime_state
from app.core.runtime_operations import target_identity


def assess_runtime_result(request: dict, plan: dict, state: dict, events) -> dict:
    result = {"observed": state, "desired_reached": False, "partial": False}
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
