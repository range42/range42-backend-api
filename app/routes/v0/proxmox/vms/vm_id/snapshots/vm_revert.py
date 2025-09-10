from typing import Any
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results

from app.schemas.proxmox.vm_id.snapshot.vm_revert import Request_ProxmoxVmsVMID_RevertSnapshot
from app.schemas.proxmox.vm_id.snapshot.vm_revert import Reply_ProxmoxVmsVMID_RevertSnapshot

from pathlib import Path
import os

#
# ISSUE - #9
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"
INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

# @router.post("/{vm_id}/start")
# def proxmox_vms_vm_id_start(
#     vm_id: int,
#     req: Request_ProxmoxVmsVMID_StartStopPauseResume,
# ):

####
# => /api/proxmox/vms/vmd_id/snapshot/revert - POST
#
@router.post(
    path="/revert",
    summary="Revert a VM to a snapshot",
    description="Reverts the specified virtual machine (VM) to the given snapshot.",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
    response_description="Snapshot revert result",
)


def proxmox_vms_vm_id_revert_snapshot(req: Request_ProxmoxVmsVMID_RevertSnapshot):

    if debug ==1:
        print("::  REQUEST ::", req.dict())
        print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
        print(f":: PLAYBOOK_SRC  :: {PLAYBOOK_SRC} ")
        print(f":: INVENTORY_SRC :: {INVENTORY_SRC} ")

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logging.error(err)
        raise HTTPException(status_code=400, detail=err)

    if not INVENTORY_SRC.exists():
        err = f":: err - MISSING INVENTORY : {INVENTORY_SRC}"
        logging.error(err)
        raise HTTPException(status_code=400, detail=err)

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    extravars = request_checks(req)

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC,
        INVENTORY_SRC,
        extravars=extravars,
        limit=extravars["hosts"],
        # limit=req.hosts,
    )

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    payload = reply_processing(events, extravars, log_plain, rc, req)

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    if rc == 0:
        status = 200
    else:
        status = 500

    return JSONResponse(payload, status_code=status)


def reply_processing(events: list[dict] | list[Any],
                     extravars: dict[Any, Any],
                     log_plain: str,
                     rc,
                     req: Request_ProxmoxVmsVMID_RevertSnapshot) -> dict[str, list | Any]:

    """ reply post-processing - json or ansible raw output """

    if req.as_json:

        ####
        ##### OUTPUT TYPE - as_json=True
        #####

        action = extravars["proxmox_vm_action"]
        result = extract_action_results(events, action)

        payload = {
            "rc": rc,
            "result": result
            # "action": action,
        }  # raw

        # payload = {"rc": rc, "action": action, "result": events}
    else:
        ####
        #### OUTPUT AS TEXT - as_json=False
        ####

        # payload = {"rc": rc, "log_plain": log_plain, "log_multiline": log_plain.splitlines()}
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return payload


def request_checks(req: Request_ProxmoxVmsVMID_RevertSnapshot) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "snapshot_vm_revert"

    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id

    extravars["vm_name"] = "admin-wazuh"  # TODO - add vm_id <> vm_name resolver func !

    if req.vm_snapshot_name is not None:
        extravars["vm_snapshot_name"] = req.vm_snapshot_name

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    # nothing :
    if not extravars:
        extravars = None

    extravars["hosts"] = "proxmox"

    if debug == 1:
        print(f", extra vars : {extravars}")
    return extravars

