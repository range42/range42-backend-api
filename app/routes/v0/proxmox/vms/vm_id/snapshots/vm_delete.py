from typing import Any
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.runner import  run_playbook_core # , extract_action_resu# lts
from app.json_extract import extract_action_results
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name

from app.schemas.proxmox.vm_id.snapshot.vm_delete import Request_ProxmoxVmsVMID_DeleteSnapshot
from app.schemas.proxmox.vm_id.snapshot.vm_delete import Reply_ProxmoxVmsVMID_DeleteSnapshot

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
# => /api/proxmox/vms/vmd_id/snapshot/delete - DELETE
#
@router.delete(
    path="/delete",
    summary="Delete a snapshot for a VM",
    description="Delete a snapshot of the specified virtual machine (VM).",
    tags=["proxmox - vm snapshots"],
    response_model=Reply_ProxmoxVmsVMID_DeleteSnapshot,
    response_description="Snapshot delete result",
)


def proxmox_vms_vm_id_delete_snapshot(req: Request_ProxmoxVmsVMID_DeleteSnapshot):

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
                     req: Request_ProxmoxVmsVMID_DeleteSnapshot) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxVmsVMID_DeleteSnapshot) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "snapshot_vm_delete"

    # if req.vm_id is not None:
    #     extravars["vm_id"] = req.vm_id

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id

    # extravars["vm_name"] = "admin-wazuh"  # TODO - add vm_id <> vm_name resolver func !
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"] )

    if req.vm_snapshot_name:
        extravars["vm_snapshot_name"] = req.vm_snapshot_name


    # nothing :
    if not extravars:
        extravars = None

    extravars["hosts"] = "proxmox"

    if debug == 1:
        print(f", extra vars : {extravars}")
    return extravars

