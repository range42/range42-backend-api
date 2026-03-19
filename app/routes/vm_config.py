"""Consolidated VM configuration routes.

Replaces: app/routes/v0/proxmox/vms/vm_id/config/*.py
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.runner import run_playbook_core
from app.extract_actions import extract_action_results
from app import utils

from app.schemas.proxmox.vm_id.config.vm_get_config import Request_ProxmoxVmsVMID_VmGetConfig, Reply_ProxmoxVmsVMID_VmGetConfig
from app.schemas.proxmox.vm_id.config.vm_get_config_cdrom import Request_ProxmoxVmsVMID_VmGetConfigCdrom, Reply_ProxmoxVmsVMID_VmGetConfigCdrom
from app.schemas.proxmox.vm_id.config.vm_get_config_cpu import Request_ProxmoxVmsVMID_VmGetConfigCpu, Reply_ProxmoxVmsVMID_VmGetConfigCpu
from app.schemas.proxmox.vm_id.config.vm_get_config_ram import Request_ProxmoxVmsVMID_VmGetConfigRam, Reply_ProxmoxVmsVMID_VmGetConfigRam
from app.schemas.proxmox.vm_id.config.vm_set_tag import Request_ProxmoxVmsVMID_VmSetTag, Reply_ProxmoxVmsVMID_VmSetTag

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
    path="/vm_get_config",
    summary="Retrieve configuration of a VM",
    description="Returns the configuration details of the specified virtual machine (VM).",
    tags=["proxmox - vm configuration"],
    response_model=Reply_ProxmoxVmsVMID_VmGetConfig,
    response_description="VM configuration details",
)
def proxmox_vms_vm_id_vm_get_config(req: Request_ProxmoxVmsVMID_VmGetConfig):
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
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.proxmox_node:
        extravars["vm_tag_name"] = req.vm_tag_name
    return _run_config_action(req, "vm_set_tag", extravars)
