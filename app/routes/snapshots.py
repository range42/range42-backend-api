"""Consolidated snapshot routes.

Endpoints
---------
- ``POST /v0/admin/proxmox/vms/vm_id/snapshot/list`` -- List snapshots.
- ``POST /v0/admin/proxmox/vms/vm_id/snapshot/create`` -- Create a snapshot.
- ``DELETE /v0/admin/proxmox/vms/vm_id/snapshot/delete`` -- Delete a snapshot.
- ``POST /v0/admin/proxmox/vms/vm_id/snapshot/revert`` -- Revert to a snapshot.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.runner import run_playbook_core
from app.core.extractor import extract_action_results
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name
from app import utils

from app.schemas.snapshots import (
    Request_ProxmoxVmsVMID_ListSnapshot, Reply_ProxmoxVmsVMID_ListSnapshot,
    Request_ProxmoxVmsVMID_CreateSnapshot, Reply_ProxmoxVmsVMID_CreateSnapshot,
    Request_ProxmoxVmsVMID_DeleteSnapshot, Reply_ProxmoxVmsVMID_DeleteSnapshot,
    Request_ProxmoxVmsVMID_RevertSnapshot, Reply_ProxmoxVmsVMID_RevertSnapshot,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

router = APIRouter()


def _run_snapshot_action(req, action: str, extravars: dict) -> JSONResponse:
    extravars["proxmox_vm_action"] = action
    extravars["hosts"] = "proxmox"

    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}")

    inventory = utils.resolve_inventory(INVENTORY_NAME)

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC, inventory, limit=extravars["hosts"], extravars=extravars,
    )

    if req.as_json:
        result = extract_action_results(events, action)
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


@router.post(
    path="/list",
    summary="List a snapshot for a VM",
    description="List snapshot of the specified virtual machine (VM).",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_ListSnapshot,
    response_description="Snapshot list result",
)
def proxmox_vms_vm_id_list_snapshot(req: Request_ProxmoxVmsVMID_ListSnapshot):
    """List all snapshots for a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    return _run_snapshot_action(req, "snapshot_vm_list", extravars)


@router.post(
    path="/create",
    summary="Create a snapshot for a VM",
    description="Creates a snapshot of the specified virtual machine (VM).",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_CreateSnapshot,
    response_description="Snapshot creation result",
)
def proxmox_vms_vm_id_create_snapshot(req: Request_ProxmoxVmsVMID_CreateSnapshot):
    """Create a named snapshot of a VM.

    :param req: Request body with node, VM ID, snapshot name, and description.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    if req.vm_snapshot_name is not None:
        extravars["vm_snapshot_name"] = req.vm_snapshot_name
    if req.vm_snapshot_description is not None:
        extravars["vm_snapshot_description"] = req.vm_snapshot_description
    return _run_snapshot_action(req, "snapshot_vm_create", extravars)


@router.delete(
    path="/delete",
    summary="Delete a snapshot for a VM",
    description="Delete a snapshot of the specified virtual machine (VM).",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_DeleteSnapshot,
    response_description="Snapshot delete result",
)
def proxmox_vms_vm_id_delete_snapshot(req: Request_ProxmoxVmsVMID_DeleteSnapshot):
    """Delete a named snapshot from a VM.

    :param req: Request body with node, VM ID, and snapshot name.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    if req.vm_snapshot_name:
        extravars["vm_snapshot_name"] = req.vm_snapshot_name
    return _run_snapshot_action(req, "snapshot_vm_delete", extravars)


@router.post(
    path="/revert",
    summary="Revert a VM to a snapshot",
    description="Reverts the specified virtual machine (VM) to the given snapshot.",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
    response_description="Snapshot revert result",
)
def proxmox_vms_vm_id_revert_snapshot(req: Request_ProxmoxVmsVMID_RevertSnapshot):
    """Revert a VM to a named snapshot.

    :param req: Request body with node, VM ID, and snapshot name.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    if req.vm_snapshot_name is not None:
        extravars["vm_snapshot_name"] = req.vm_snapshot_name
    return _run_snapshot_action(req, "snapshot_vm_revert", extravars)
