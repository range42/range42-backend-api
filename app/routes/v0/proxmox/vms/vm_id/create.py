from typing import Any
import logging
from fastapi import APIRouter, HTTPException, Body

from fastapi.responses import JSONResponse

from app.schemas.proxmox.vm_id.create import Request_ProxmoxVmsVMID_Create
from app.schemas.proxmox.vm_id.create import Reply_ProxmoxVmsVMID_Create

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
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

#
# => /v0/admin/proxmox/vms/vmd_id/create - POST
#

@router.post(
    path="/create",
    summary="Create a specific VM",
    description="This endpoint create the target virtual machine (VM).",
    tags=["proxmox - vm management"],
    #
    response_model=Reply_ProxmoxVmsVMID_Create,
    response_description="Delete result",
)

def proxmox_vms_vm_id_create(req: Request_ProxmoxVmsVMID_Create):
    """ This endpoint clone the target virtual machine (VM)."""

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    checked_playbook_filepath  = PLAYBOOK_SRC
    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)

    if debug ==1:
        print("")
        print("::  REQUEST ::", req.model_dump())
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
                     req: Request_ProxmoxVmsVMID_Create) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxVmsVMID_Create) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    extravars["proxmox_vm_action"] = "vm_create"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

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


    # if req.vm_description is not None:
    #     extravars["vm_description"] = req.vm_description


    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    extravars["hosts"] = "proxmox"
    return extravars

