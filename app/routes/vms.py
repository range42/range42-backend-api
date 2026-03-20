"""Consolidated VM lifecycle routes.

Endpoints
---------
- ``POST /v0/admin/proxmox/vms/list`` -- List VMs and LXC containers.
- ``POST /v0/admin/proxmox/vms/list_usage`` -- Resource usage of VMs.
- ``POST /v0/admin/proxmox/vms/vm_id/start`` -- Start a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/stop`` -- Stop a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/stop_force`` -- Force stop a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/pause`` -- Pause a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/resume`` -- Resume a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/create`` -- Create a VM.
- ``DELETE /v0/admin/proxmox/vms/vm_id/delete`` -- Delete a VM.
- ``POST /v0/admin/proxmox/vms/vm_id/clone`` -- Clone a VM.
- ``POST /v0/admin/proxmox/vms/vm_ids/start`` -- Mass start VMs.
- ``POST /v0/admin/proxmox/vms/vm_ids/stop`` -- Mass stop VMs.
- ``POST /v0/admin/proxmox/vms/vm_ids/stop_force`` -- Mass force stop VMs.
- ``POST /v0/admin/proxmox/vms/vm_ids/pause`` -- Mass pause VMs.
- ``POST /v0/admin/proxmox/vms/vm_ids/resume`` -- Mass resume VMs.
- ``DELETE /v0/admin/proxmox/vms/vm_ids/delete`` -- Mass delete VMs.
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.runner import run_playbook_core
from app.core.extractor import extract_action_results
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name
from app import utils

from app.schemas.vms import (
    Request_ProxmoxVms_VmList, Reply_ProxmoxVmList,
    Request_ProxmoxVms_VmListUsage, Reply_ProxmoxVms_VmListUsage,
    Request_ProxmoxVmsVMID_StartStopPauseResume, Reply_ProxmoxVmsVMID_StartStopPauseResume,
    Request_ProxmoxVmsVMID_Create, Reply_ProxmoxVmsVMID_Create,
    Request_ProxmoxVmsVMID_Delete, Reply_ProxmoxVmsVMID_Delete,
    Request_ProxmoxVmsVMID_Clone, Reply_ProxmoxVmsVMID_Clone,
    Request_ProxmoxVmsVmIds_MassStartStopPauseResume,
    Request_ProxmoxVmsVmIds_MassDelete, Reply_ProxmoxVmsVmIds_MassDelete,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run_proxmox_action(req, action: str, extravars: dict) -> JSONResponse:
    """Common pattern for all standard Proxmox action routes."""
    extravars["proxmox_vm_action"] = action
    extravars["hosts"] = "proxmox"

    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}")

    inventory = utils.resolve_inventory(INVENTORY_NAME)

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC,
        inventory,
        limit=extravars["hosts"],
        extravars=extravars,
    )

    if req.as_json:
        result = extract_action_results(events, action)
        payload: dict[str, Any] = {"rc": rc, "result": result}
        # On failure, include error context from Ansible logs
        if rc != 0:
            lines = log_plain.splitlines()
            fatal = next((l for l in lines if "fatal:" in l or "FAILED" in l), None)
            payload["error"] = fatal.strip() if fatal else f"Ansible exited with rc={rc}"
            payload["log_multiline"] = lines[-10:]  # last 10 lines for context
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


# ===========================================================================
# Router 1: /v0/admin/proxmox/vms  (list, list_usage)
# ===========================================================================
vms_router = APIRouter()


@vms_router.post(
    path="/list",
    summary="List VMs and LXC containers",
    description="This endpoint retrieves all virtual machines (VMs) and LXC containers from Proxmox.",
    tags=["proxmox - usage"],
    response_model=Reply_ProxmoxVmList,
    response_description="List VM result",
)
def proxmox_vms_list(req: Request_ProxmoxVms_VmList):
    """List all VMs and LXC containers on the Proxmox node.

    :param req: Request body with optional ``proxmox_node`` filter.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    return _run_proxmox_action(req, "vm_list", extravars)


@vms_router.post(
    path="/list_usage",
    summary="Retrieve current resource usage of VMs and LXC containers",
    description="Returns the current RAM, disk, and CPU usage for all virtual machines (VMs) and LXC containers.",
    tags=["proxmox - usage"],
    response_model=Reply_ProxmoxVms_VmListUsage,
    response_description="Resource usage details",
)
def proxmox_vms_list_usage(req: Request_ProxmoxVms_VmListUsage):
    """Retrieve current resource usage (RAM, CPU, disk) for all VMs.

    :param req: Request body with optional ``proxmox_node`` filter.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    return _run_proxmox_action(req, "vm_list_usage", extravars)


# ===========================================================================
# Router 2: /v0/admin/proxmox/vms/vm_id  (lifecycle + management)
# ===========================================================================
vm_id_router = APIRouter()


@vm_id_router.post(
    path="/start",
    summary="Start a specific VM",
    description="This endpoint start the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)
def proxmox_vms_vm_id_start(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """Start a specific VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_proxmox_action(req, "vm_start", extravars)


@vm_id_router.post(
    path="/stop",
    summary="Stop a specific VM",
    description="This endpoint stop the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)
def proxmox_vms_vm_id_stop(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """Stop a specific VM gracefully.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_proxmox_action(req, "vm_stop", extravars)


@vm_id_router.post(
    path="/stop_force",
    summary="Force stop a specific VM",
    description="This endpoint force stop the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)
def proxmox_vms_vm_id_stop_force(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """Force stop a specific VM (equivalent to power off).

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_proxmox_action(req, "vm_stop_force", extravars)


@vm_id_router.post(
    path="/pause",
    summary="Pause a specific VM",
    description="This endpoint pauses the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)
def proxmox_vms_vm_id_pause(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """Pause a specific VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_proxmox_action(req, "vm_pause", extravars)


@vm_id_router.post(
    path="/resume",
    summary="Resume a specific VM",
    description="This endpoint resume the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)
def proxmox_vms_vm_id_resume(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """Resume a paused VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_proxmox_action(req, "vm_resume", extravars)


@vm_id_router.post(
    path="/create",
    summary="Create a specific VM",
    description="This endpoint create the target virtual machine (VM).",
    tags=["proxmox - vm management"],
    response_model=Reply_ProxmoxVmsVMID_Create,
    response_description="Delete result",
)
def proxmox_vms_vm_id_create(req: Request_ProxmoxVmsVMID_Create):
    """Create a new VM with the specified configuration.

    :param req: Request body with node, VM ID, name, CPU, memory, disk, and ISO options.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_name is not None:
        extravars["vm_name"] = req.vm_name
    if req.vm_cpu is not None:
        extravars["vm_cpu"] = req.vm_cpu
    if req.vm_cores is not None:
        extravars["vm_cores"] = req.vm_cores
    if req.vm_sockets is not None:
        extravars["vm_sockets"] = req.vm_sockets
    if req.vm_memory is not None:
        extravars["vm_memory"] = req.vm_memory
    if req.vm_disk_size is not None:
        extravars["vm_disk_size"] = req.vm_disk_size
    if req.vm_iso is not None:
        extravars["vm_iso"] = req.vm_iso
    return _run_proxmox_action(req, "vm_create", extravars)


@vm_id_router.delete(
    path="/delete",
    summary="Delete a specific VM",
    description="This endpoint delete the target virtual machine (VM).",
    tags=["proxmox - vm management"],
    response_model=Reply_ProxmoxVmsVMID_Delete,
    response_description="Delete result",
)
def proxmox_vms_vm_id_delete(req: Request_ProxmoxVmsVMID_Delete):
    """Delete a specific VM. Resolves the VM name before deletion.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    return _run_proxmox_action(req, "vm_delete", extravars)


@vm_id_router.post(
    path="/clone",
    summary="Clone a specific VM",
    description="This endpoint clone the target virtual machine (VM).",
    tags=["proxmox - vm management"],
    response_model=Reply_ProxmoxVmsVMID_Clone,
    response_description="Delete result",
)
def proxmox_vms_vm_id_clone(req: Request_ProxmoxVmsVMID_Clone):
    """Clone a VM to create a new VM with a different ID and name.

    :param req: Request body with source VM ID, new VM ID, name, and description.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    if req.vm_new_id is not None:
        extravars["vm_new_id"] = req.vm_new_id
    if req.vm_name is not None:
        extravars["vm_name"] = req.vm_name
    if req.vm_description is not None:
        extravars["vm_description"] = req.vm_description
    return _run_proxmox_action(req, "vm_clone", extravars)


# ===========================================================================
# Router 3: /v0/admin/proxmox/vms/vm_ids  (mass operations)
# ===========================================================================
vm_ids_router = APIRouter()


def _run_mass_action(req, action_name: str, proxmox_vm_action: str) -> JSONResponse:
    """Helper for mass start/stop/pause/resume."""
    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_filepath = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {}
    extravars["PROXMOX_VM_ACTION"] = proxmox_vm_action
    if req.proxmox_node:
        extravars["PROXMOX_NODE"] = req.proxmox_node
    if req.vm_ids:
        extravars["VM_IDS"] = req.vm_ids

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    if req.as_json:
        result = extract_action_results(events, proxmox_vm_action)
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


_MASS_ACTION_NAME = "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-vuln"


@vm_ids_router.post(
    path="/stop",
    summary="Mass stop vms ",
    description="Stop all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_stop(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):
    """Stop multiple VMs by ID list.

    :param req: Request body with ``proxmox_node`` and ``vm_ids`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    return _run_mass_action(req, _MASS_ACTION_NAME, "vm_stop")


@vm_ids_router.post(
    path="/stop_force",
    summary="Mass force stop vms ",
    description="Force stop all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_stop_force(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):
    """Force stop multiple VMs by ID list.

    :param req: Request body with ``proxmox_node`` and ``vm_ids`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    return _run_mass_action(req, _MASS_ACTION_NAME, "vm_stop_force")


@vm_ids_router.post(
    path="/start",
    summary="Mass start vms ",
    description="Start all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_start(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):
    """Start multiple VMs by ID list.

    :param req: Request body with ``proxmox_node`` and ``vm_ids`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    return _run_mass_action(req, _MASS_ACTION_NAME, "vm_start")


@vm_ids_router.post(
    path="/pause",
    summary="Mass pause vms ",
    description="Pause all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_pause(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):
    """Pause multiple VMs by ID list.

    :param req: Request body with ``proxmox_node`` and ``vm_ids`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    return _run_mass_action(req, _MASS_ACTION_NAME, "vm_pause")


@vm_ids_router.post(
    path="/resume",
    summary="Mass resume vms ",
    description="Resume all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_resume(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):
    """Resume multiple paused VMs by ID list.

    :param req: Request body with ``proxmox_node`` and ``vm_ids`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    return _run_mass_action(req, _MASS_ACTION_NAME, "vm_resume")


@vm_ids_router.delete(
    path="/delete",
    summary="Mass delete vms ",
    description="Delete all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVmIds_MassDelete,
)
def proxmox_vms_vm_ids_mass_delete(req: Request_ProxmoxVmsVmIds_MassDelete):
    """Delete multiple VMs by name/ID pairs.

    :param req: Request body with ``proxmox_node`` and ``vms`` list.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    action_name = "core/proxmox/configure/default/vms/delete-vms-vuln"
    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_filepath = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {}
    if req.proxmox_node:
        extravars["PROXMOX_NODE"] = req.proxmox_node
    if getattr(req, "vms", None):
        extravars["VMS"] = [{"ID": vm.id, "NAME": vm.name} for vm in req.vms]

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    if req.as_json:
        extravars["proxmox_vm_action"] = "vm_delete"
        result = extract_action_results(events, "vm_delete")
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)
