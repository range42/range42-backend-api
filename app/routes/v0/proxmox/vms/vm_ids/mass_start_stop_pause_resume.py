from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.proxmox.vm_ids.mass_start_stop_resume_pause import Request_ProxmoxVmsVmIds_MassStartStopPauseResume
from app.schemas.proxmox.vm_id.start_stop_resume_pause import Reply_ProxmoxVmsVMID_StartStopPauseResume

from app.runner import run_playbook_core  # , extract_action_results
from app.extract_actions import extract_action_results
from app import utils
from pathlib import Path
import os


debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

#
# => /v0/admin/proxmox/vms/vmd_ids/stop
#

@router.post(
    path="/stop",
    summary="Mass stop vms ",
    description="Stop all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_stop(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):

    action_name = "core/proxmox/configure/default/vms/stop-vms-vuln"
    #
    return proxmox_vms_vm_ids_mass_run(req, action_name, "vm_stop" )


########################################################################################################################

@router.post(
    path="/stop_force",
    summary="Mass force stop vms ",
    description="Force stop all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_stop(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):

    action_name = "core/proxmox/configure/default/vms/stop-vms-vuln"
    #
    return proxmox_vms_vm_ids_mass_run(req, action_name, "vm_stop_force")

########################################################################################################################

@router.post(
    path="/start",
    summary="Mass start vms ",
    description="Start all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_start(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):

    action_name = "core/proxmox/configure/default/vms/start-vms-vuln"
    #
    #
    return proxmox_vms_vm_ids_mass_run(req, action_name, "vm_start")


########################################################################################################################

@router.post(
    path="/pause",
    summary="Mass pause vms ",
    description="Pause all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_pause(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):

    action_name = "core/proxmox/configure/default/vms/pause-vms-vuln"
    #

    return proxmox_vms_vm_ids_mass_run(req, action_name, "vm_pause")


########################################################################################################################

@router.post(
    path="/resume",
    summary="Mass resume vms ",
    description="Resume all specified virtual machines",
    tags=["proxmox - vm lifecycle"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def proxmox_vms_vm_ids_mass_resume(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume):

    action_name = "core/proxmox/configure/default/vms/resume-vms-vuln"
    #
    #
    return proxmox_vms_vm_ids_mass_run(req, action_name, "vm_resume")

########################################################################################################################
########################################################################################################################
########################################################################################################################


def proxmox_vms_vm_ids_mass_run(
        req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume,
        action_name: str,
        proxmox_vm_action: str) -> JSONResponse:



    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    # checked_playbook_filepath  = utils.resolve_actions_playbook(action_name, "public_github")
    checked_playbook_filepath = utils.resolve_bundles_playbook(action_name, "public_github")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    if debug == 1:
        print("::  ACTION_NAME", action_name)
        print("::  REQUEST ::", req.model_dump())
        print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
        print(f":: checked_inventory_filepath :: {checked_inventory_filepath} ")
        print(f":: checked_playbook_filepath  :: {checked_playbook_filepath} ")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    extravars = request_checks(req, proxmox_vm_action)

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        limit=req.proxmox_node,
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
                     req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxVmsVmIds_MassStartStopPauseResume,
                   proxmox_vm_action: str) -> dict[Any, Any]:

    extravars = {}

    extravars["proxmox_vm_action"] = proxmox_vm_action # "vm_stop_force"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    #
    if req.vm_ids:
        extravars["vm_ids"] = req.vm_ids

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    # extravars["hosts"] = "proxmox"

    return extravars

