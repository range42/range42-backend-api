"""Consolidated VM configuration routes.

Endpoints
---------
- ``POST /v0/admin/proxmox/vms/vm_id/config/vm_get_config`` -- Full VM config.
- ``POST /v0/admin/proxmox/vms/vm_id/config/vm_get_config_cdrom`` -- CD-ROM config.
- ``POST /v0/admin/proxmox/vms/vm_id/config/vm_get_config_cpu`` -- CPU config.
- ``POST /v0/admin/proxmox/vms/vm_id/config/vm_get_config_ram`` -- RAM config.
- ``POST /v0/admin/proxmox/vms/vm_id/config/vm_set_tag`` -- Set VM tags.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import utils
from app.core.extractor import extract_action_results
from app.core.runner import run_playbook_core
from app.schemas.vm_config import (
    Reply_ProxmoxVmsVMID_VmGetConfig,
    Reply_ProxmoxVmsVMID_VmGetConfigCdrom,
    Reply_ProxmoxVmsVMID_VmGetConfigCpu,
    Reply_ProxmoxVmsVMID_VmGetConfigRam,
    Reply_ProxmoxVmsVMID_VmSetTag,
    Request_ProxmoxVmsVMID_VmGetConfig,
    Request_ProxmoxVmsVMID_VmGetConfigCdrom,
    Request_ProxmoxVmsVMID_VmGetConfigCpu,
    Request_ProxmoxVmsVMID_VmGetConfigRam,
    Request_ProxmoxVmsVMID_VmSetTag,
    Request_ProxmoxVmsVMID_VmSetName,
    Reply_ProxmoxVmsVMID_VmSetName,
    Request_ProxmoxVmsVMID_VmSetDescription,
    Reply_ProxmoxVmsVMID_VmSetDescription,
    Request_ProxmoxVmsVMID_VmSetCpu,
    Reply_ProxmoxVmsVMID_VmSetCpu,
    Request_ProxmoxVmsVMID_VmSetMemory,
    Reply_ProxmoxVmsVMID_VmSetMemory,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

router = APIRouter()


def _run_config_action(req, action: str, extravars: dict) -> JSONResponse:
    """Common pattern for VM config routes."""
    extravars["proxmox_vm_action"] = action
    extravars["hosts"] = "proxmox"

    if not PLAYBOOK_SRC.exists():
        raise HTTPException(
            status_code=400, detail=f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        )

    inventory = utils.resolve_inventory(INVENTORY_NAME)

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC,
        inventory,
        limit=extravars["hosts"],
        extravars=extravars,
    )

    if req.as_json:
        result = extract_action_results(events, action)
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


@router.post(
    path="/vm_get_config",
    summary="Retrieve configuration of a VM",
    description="Returns the configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmGetConfig,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_get_config(req: Request_ProxmoxVmsVMID_VmGetConfig):
    """Retrieve the full configuration of a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_config_action(req, "vm_get_config", extravars)


@router.post(
    path="/vm_get_config_cdrom",
    summary="Get cdrom configuration of a VM",
    description="Returns the cdrom configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmGetConfigCdrom,
    response_description="cdrom configuration details",
)
def proxmox_vms_vm_id_vm_get_config_cdrom(req: Request_ProxmoxVmsVMID_VmGetConfigCdrom):
    """Retrieve the CD-ROM configuration of a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_config_action(req, "vm_get_config_cdrom", extravars)


@router.post(
    path="/vm_get_config_cpu",
    summary="Get cpu configuration of a VM",
    description="Returns the cpu configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmGetConfigCpu,
    response_description="cpu configuration details",
)
def proxmox_vms_vm_id_vm_get_config_cpu(req: Request_ProxmoxVmsVMID_VmGetConfigCpu):
    """Retrieve the CPU configuration of a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_config_action(req, "vm_get_config_cpu", extravars)


@router.post(
    path="/vm_get_config_ram",
    summary="Get ram configuration of a VM",
    description="Returns the ram configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmGetConfigRam,
    response_description="ram configuration details",
)
def proxmox_vms_vm_id_vm_get_config_ram(req: Request_ProxmoxVmsVMID_VmGetConfigRam):
    """Retrieve the RAM configuration of a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_config_action(req, "vm_get_config_ram", extravars)


@router.post(
    path="/vm_set_tag",
    summary="Retrieve configuration of a VM",
    description="Returns the configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmSetTag,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_set_tags(req: Request_ProxmoxVmsVMID_VmSetTag):
    """Set tags on a VM.

    :param req: Request body with ``proxmox_node``, ``vm_id``, and ``vm_tag_name``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.proxmox_node:
        extravars["vm_tag_name"] = req.vm_tag_name
    return _run_config_action(req, "vm_set_tag", extravars)


@router.post(
    path="/vm_set_name",
    summary="Set name of a VM",
    description="Sets the name of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmSetName,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_set_name(req: Request_ProxmoxVmsVMID_VmSetName):
    """Set name on a VM.

    :param req: Request body with ``proxmox_node``, ``vm_id``, and ``vm_name``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_name:
        extravars["vm_name"] = req.vm_name
    return _run_config_action(req, "vm_set_name", extravars)


@router.post(
    path="/vm_set_description",
    summary="Set description of a VM",
    description="Sets the description of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmSetDescription,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_set_description(req: Request_ProxmoxVmsVMID_VmSetDescription):
    """Set description on a VM.

    :param req: Request body with ``proxmox_node``, ``vm_id``, and ``vm_description``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_description:
        extravars["vm_description"] = req.vm_description
    return _run_config_action(req, "vm_set_description", extravars)


@router.post(
    path="/vm_set_cpu",
    summary="Set CPU cores of a VM",
    description="Sets the CPU cores of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmSetCpu,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_set_cpu(req: Request_ProxmoxVmsVMID_VmSetCpu):
    """Set CPU cores on a VM.

    :param req: Request body with ``proxmox_node``, ``vm_id``, and ``vm_cores``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_cores is not None:
        extravars["vm_cores"] = str(req.vm_cores)
    return _run_config_action(req, "vm_set_cpu", extravars)


@router.post(
    path="/vm_set_memory",
    summary="Set memory of a VM",
    description="Sets the memory of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmSetMemory,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_set_memory(req: Request_ProxmoxVmsVMID_VmSetMemory):
    """Set memory on a VM.

    :param req: Request body with ``proxmox_node``, ``vm_id``, and ``vm_memory``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_memory is not None:
        extravars["vm_memory"] = str(req.vm_memory)
    return _run_config_action(req, "vm_set_memory", extravars)
