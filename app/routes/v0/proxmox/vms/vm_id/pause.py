from typing import Any
from venv import logger
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.proxmox.vm_id.start_stop_resume_pause import Request_ProxmoxVmsVMID_StartStopPauseResume
from app.schemas.proxmox.vm_id.start_stop_resume_pause import Reply_ProxmoxVmsVMID_StartStopPauseResume

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #3
#

debug = 0

router = APIRouter()

# PROJECT_ROOT = Path(__file__).resolve().parents[6]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

# INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

# @router.post("/{vm_id}/start")
# def proxmox_vms_vm_id_start(
#     vm_id: int,
#     req: Request_ProxmoxVmsVMID_StartStopPauseResume,
# ):

#
# => /api/proxmox/vms/vmd_id/pause
#
@router.post(
    path="/pause",
    summary="Pause a specific VM",
    description="This endpoint pauses the target virtual machine (VM).",
    tags=["proxmox - vm lifecycle"],
    #
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    response_description="Start result",
)

def proxmox_vms_vm_id_pause(req: Request_ProxmoxVmsVMID_StartStopPauseResume):
    """ This endpoint pauses the target virtual machine (VM)."""

    # if debug ==1:
    #     print("::  REQUEST ::", req.dict())
    #     print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
    #     # print(f":: PLAYBOOK_SRC  :: {PLAYBOOK_SRC} ")
    #     # print(f":: INVENTORY_SRC :: {INVENTORY_SRC} ")

    #
    # if not INVENTORY_SRC.exists():
    #     err = f":: err - MISSING INVENTORY : {INVENTORY_SRC}"
    #     logger.error(err)
    #     raise HTTPException(status_code=400, detail=err)
    #
    #

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    checked_playbook_filepath  = PLAYBOOK_SRC
    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)


    if debug ==1:
        print("")
        print("::  REQUEST ::", req.dict())
        print(f":: checked_inventory_filepath :: {checked_inventory_filepath} ")
        print(f":: checked_playbook_filepath  :: {checked_playbook_filepath} ")
        print("")


    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    extravars = request_checks(req)

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        # limit=req.hosts,
        limit=extravars["hosts"],
        extravars=extravars,
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
                     req: Request_ProxmoxVmsVMID_StartStopPauseResume) -> dict[str, list | Any]:

    """ reply post-processing - json or ansible raw output """

    if req.as_json:

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


def request_checks(req: Request_ProxmoxVmsVMID_StartStopPauseResume) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "vm_pause"

    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    extravars["hosts"] = "proxmox"
    return extravars

