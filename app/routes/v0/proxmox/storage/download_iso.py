from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.proxmox.vm_id.config.vm_get_config import Request_ProxmoxVmsVMID_VmGetConfig
from app.schemas.proxmox.vm_id.config.vm_get_config import Reply_ProxmoxVmsVMID_VmGetConfig


from app.schemas.proxmox.storage.download_iso import Request_ProxmoxStorage_DownloadIso
from app.schemas.proxmox.storage.download_iso import Reply_ProxmoxStorage_DownloadIsoItem



from app.runner import  run_playbook_core # , extract_action_results
from app.extract_actions import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #17
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[6]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

#
# => /v0/admin/proxmox/vms/vmd_id/vm_get_config
#
@router.post(
    path="/download_iso",
    summary="Retrieve configuration of a VM",
    description="Returns the configuration details of the specified virtual machine (VM).",
    tags=["proxmox - storage"],
    response_model=Reply_ProxmoxStorage_DownloadIsoItem,
    response_description="VM configuration details",
)

def proxmox_storage_download_iso(req: Request_ProxmoxStorage_DownloadIso):

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logging.error(err)
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
                     req: Request_ProxmoxStorage_DownloadIso) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxStorage_DownloadIso) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "storage_download_iso"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.proxmox_storage is not None:
        extravars["proxmox_storage"] = req.proxmox_storage

    # if req.storage_name is not None:
    #     extravars["storage_name"] = req.storage_name

    if req.iso_file_content_type is not None:
        extravars["iso_file_content_type"] = req.iso_file_content_type

    if req.iso_file_name is not None:
        extravars["iso_file_name"] = req.iso_file_name

    if req.iso_url is not None:
        extravars["iso_url"] = req.iso_url


    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    extravars["hosts"] = "proxmox"
    return extravars

