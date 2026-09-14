"""Build backend-owned wrappers around the installed runtime composites."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path

import yaml

from app.core import db
from app.core.models import ProxmoxHost
from app.core.preflight import check_vmids
from app.core.runtime_operations import blocked, operation_profile, plan_operation, target_identity
from app.core.runtime_state import read_runtime_state, runtime_targets


@dataclass(frozen=True)
class RuntimeRun:
    playbook: Path
    inventory: Path
    config_dir: Path
    scenario_dir: Path
    plan: dict


def _private_document(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        yaml.safe_dump(document, stream, sort_keys=False)


async def _verified_plan(deployment, attempt, host, scenario_dir) -> dict:
    operation = attempt.operation
    if (not operation or operation.get("project_sha") != deployment.project_sha
            or attempt.project_sha != deployment.project_sha
            or operation.get("target_host_id") != deployment.target_host_id or host is None
            or host.id != deployment.target_host_id):
        raise blocked("The deployment target or project revision changed after this operation was requested", "RUNTIME_TARGET_CHANGED")
    async with db.get_session_factory()() as session:
        fresh_host = await session.get(ProxmoxHost, deployment.target_host_id)
    if (operation.get("target_identity") != target_identity(host)
            or operation.get("target_identity") != target_identity(fresh_host)):
        raise blocked("The target API address or node changed; review the current host and request a new operation", "RUNTIME_TARGET_CHANGED")
    request = operation["request"]
    profile = await asyncio.to_thread(operation_profile, request["kind"])
    if profile != operation.get("runtime"):
        raise blocked("The installed runtime changed; request this operation again after reviewing the new release", "RUNTIME_REVISION_CHANGED")
    state = await read_runtime_state(scenario_dir, host, deployment_id=deployment.id)
    plan = plan_operation(request, state)
    overrides = json.loads(host.protected_vmids_override_json) if host.protected_vmids_override_json else None
    check = check_vmids(plan["vmids"], host_overrides=overrides)
    if check.result == "block":
        raise blocked(check.detail, "VMID_PROTECTED")
    return plan


def _ownership_guard(vms: list[dict], deployment_id: str) -> dict:
    """Recheck every exact ownership marker in the runner before the composite."""
    return {
        "name": "Verify deployment ownership before runtime mutation", "hosts": "proxmox", "gather_facts": False,
        "vars": {"r42_runtime_guests": [{"vm_id": vm["vm_id"], "vm_name": vm["vm_name"]} for vm in vms]},
        "tasks": [
            {"name": "Read owned guest configuration", "ansible.builtin.uri": {
                "url": "https://{{ proxmox_api_host }}/api2/json/nodes/{{ proxmox_node }}/qemu/{{ r42_owned_guest.vm_id }}/config",
                "headers": {"Authorization": "PVEAPIToken={{ proxmox_api_user }}!{{ proxmox_api_token_id }}={{ proxmox_api_token_secret }}"},
                "method": "GET", "validate_certs": True,
                "ca_path": "{{ lookup('env', 'RANGE42_PROXMOX_CA_FILE') | default(omit, true) }}",
            }, "loop": "{{ r42_runtime_guests }}", "loop_control": {"loop_var": "r42_owned_guest"},
             "register": "r42_runtime_configs", "no_log": True},
            {"name": "Refuse reassigned or template guests", "ansible.builtin.assert": {"that": [
                "r42_owned_config.json.data.name == r42_owned_config.r42_owned_guest.vm_name",
                "not (r42_owned_config.json.data.template | default(false) | bool)",
                f"'{deployment_id}' == r42_deployment_id",
                f"'range42-deployment:{deployment_id}' in (r42_owned_config.json.data.description | default('')).splitlines()",
            ], "fail_msg": "Guest ownership changed; no runtime mutation is allowed"},
             "loop": "{{ r42_runtime_configs.results }}", "loop_control": {"loop_var": "r42_owned_config"}, "no_log": True},
        ],
    }


def _ssh_node_guard() -> dict:
    """An API entrypoint can proxy another node; SNAT must use the chosen node."""
    return {
        "name": "Verify SSH target before shared SDN mutation", "hosts": "proxmox", "gather_facts": False,
        "tasks": [
            {"name": "Read the actual SSH node name", "ansible.builtin.command": {"argv": ["hostname", "-s"]},
             "delegate_to": "r42-proxmox-cli", "changed_when": False, "register": "r42_runtime_ssh_node"},
            {"name": "Refuse SDN reconciliation on another cluster member", "ansible.builtin.assert": {
                "that": ["r42_runtime_ssh_node.stdout | trim == proxmox_node"],
                "fail_msg": "The SSH destination differs from the selected Proxmox node. Register that node's own API address before changing SNAT.",
            }},
        ],
    }


async def prepare_runtime_run(deployment, attempt, host, scenario, artifact_dir: Path) -> RuntimeRun:
    scenario_dir = scenario.playbook.parent
    plan = await _verified_plan(deployment, attempt, host, scenario_dir)
    root = Path(os.environ["RANGE42_BUNDLE_DIR"]).resolve()
    bundle = root / plan["bundle"] / "main.yml"
    if bundle.is_symlink() or not bundle.is_file() or not bundle.resolve().is_relative_to(root):
        raise blocked("The requested composite bundle is unavailable", "RUNTIME_CAPABILITY_MISSING")
    directory = artifact_dir / "runtime"
    directory.mkdir(mode=0o700)
    config_dir = directory / "config"
    config_dir.mkdir(mode=0o700)
    # Only the manifest is new. The original private credentials remain in
    # the locked workspace and are never copied into the project checkout.
    (config_dir / "secrets").symlink_to(Path(deployment.workspace_path) / "secrets", target_is_directory=True)
    vms, _ = runtime_targets(scenario_dir)
    targets = [vm for vm in vms if vm["vm_id"] in plan["vmids"]]
    manifest = config_dir / "scenario/manifest/scenario_vms.json"
    manifest.parent.mkdir(parents=True, mode=0o700)
    with os.fdopen(os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        json.dump({"scenario": deployment.scenario_label, "version": 2, "vms": targets}, stream)
    inventory = directory / "hosts.yml"
    _private_document(inventory, {"all": {"children": {
        "proxmox": {"hosts": {"r42-proxmox": {"ansible_connection": "local",
                     "ansible_python_interpreter": "{{ ansible_playbook_python }}"}}},
        "proxmox_cli": {"hosts": {"r42-proxmox-cli": {"ansible_host": "{{ r42_proxmox_address }}",
                         "ansible_user": "{{ r42_proxmox_ssh_user }}", "ansible_ssh_common_args":
                         "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={{ deployer_cli_user_ssh_known_hosts | quote }}"}}},
    }}})
    plays = [_ownership_guard(targets, deployment.id)] if targets else []
    if attempt.operation["request"]["kind"] == "sdn_snat":
        plays.append(_ssh_node_guard())
    plays.append({"ansible.builtin.import_playbook": str(bundle), "vars": plan["variables"]})
    playbook = directory / "main.yml"
    _private_document(playbook, plays)
    _private_document(directory / "context.yml", {"scenario_dir": str(scenario_dir.relative_to(artifact_dir)), "plan": plan})
    return RuntimeRun(playbook, inventory, config_dir, scenario_dir, plan)


async def recheck_runtime_run(deployment, attempt, host, run: RuntimeRun) -> None:
    # SSH/vault preparation may take time. Recheck ownership, shared pending
    # state and installed provenance immediately before starting the runner.
    if await _verified_plan(deployment, attempt, host, run.scenario_dir) != run.plan:
        raise blocked("Runtime targets changed during preparation; review current state and retry", "RUNTIME_TARGET_CHANGED")
