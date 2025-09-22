from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.bundles.core.proxmox.configure.default.vms.start_stop_resume_pause_default import Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms
from app.schemas.proxmox.vm_id.start_stop_resume_pause import Reply_ProxmoxVmsVMID_StartStopPauseResume

from app.runner import run_playbook_core  # , extract_action_results
from app.extract_actions import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #30
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

#
# => /v0/admin/run/bundles/core/proxmox/configure/default/start-vms-admin
#
@router.post(
    path="core/proxmox/configure/default/start-vms-admin",
    summary="Start student vms ",
    description="Start all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def bundles_proxmox_configure_default_vms_stop_vms_vuln(req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_run(req,
                                                     "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-admin",
                                                     "vm_start"
                                                     )

#######################################################################################################################

#
# => /v0/admin/run/bundles/core/proxmox/configure/default/stop-vms-admin
#
@router.post(
    path="core/proxmox/configure/default/stop-vms-admin",
    summary="Stop student vms ",
    description="Stop all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def bundles_proxmox_configure_default_vms_stop_vms_vuln(req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_run(req, "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-admin",
                                                     "vm_stop"
                                                     )


#######################################################################################################################

#
# => /v0/admin/run/bundles/core/proxmox/configure/default/pause-vms-admin
#
@router.post(
    path="core/proxmox/configure/default/pause-vms-admin",
    summary="Pause student vms ",
    description="Pause all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def bundles_proxmox_configure_default_vms_stop_vms_vuln(req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_run(req, "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-admin",
                                                     "vm_pause"
                                                     )


#######################################################################################################################

#
# => /v0/admin/run/bundles/core/proxmox/configure/default/resume-vms-admin
#
@router.post(
    path="core/proxmox/configure/default/resume-vms-admin",
    summary="Resume student vms ",
    description="Resume all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
)
def bundles_proxmox_configure_default_vms_stop_vms_vuln(req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_run(req, "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-admin",
                                                     "vm_resume"
                                                     )


def bundles_proxmox_configure_default_vms_run(
        req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
        action_name: str,
        proxmox_vm_action: str) -> JSONResponse:

    #
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
                     req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms) -> dict[str, list | Any]:

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



def request_checks(req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
                   proxmox_vm_action: str) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    extravars["proxmox_vm_action"] = proxmox_vm_action # "vm_stop_force"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    #
    # if req.vm_ids:
    #     extravars["vm_ids"] = req.vm_ids

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    # extravars["hosts"] = "proxmox"

    return extravars

