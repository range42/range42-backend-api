from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

# from app.schemas.bundles.core.proxmox.configure.default.vms.start_stop_resume_pause_default import Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms
from app.schemas.bundles.core.proxmox.configure.default.vms.revert_snapshot_default import  Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms
from app.schemas.proxmox.vm_id.snapshot.vm_revert import Reply_ProxmoxVmsVMID_RevertSnapshot

from app.utils.vm_id_name_resolver import resolv_id_to_vm_name

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
# => /v0/admin/run/bundles/core/proxmox/configure/default/snapshot-revert-vms-student
#
@router.post(
    path="/core/proxmox/configure/default/snapshot/revert-vms-student",
    summary="Snapshot student vms ",
    description="Snapshot all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - student"],
    response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
)
def bundles_proxmox_configure_default_vms_stop_vms_student(req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_revert_snapshot(req, "core/proxmox/configure/default/vms/snapshot/revert-vms-student")
#
# => /v0/admin/run/bundles/core/proxmox/configure/default/snapshot-revert-vms-vuln
#
@router.post(
    path="/core/proxmox/configure/default/snapshot/revert-vms-vuln",
    summary="Snapshot student vms ",
    description="Snapshot all vulnerable virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - vuln"],
    response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
)
def bundles_proxmox_configure_default_vms_stop_vms_vuln(req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_revert_snapshot(req, "core/proxmox/configure/default/vms/snapshot/revert-vms-vuln")

#
# => /v0/admin/run/bundles/core/proxmox/configure/default/admin-vms-admin
#
@router.post(
    path="/core/proxmox/configure/default/snapshot/revert-vms-admin",
    summary="Snapshot admin vms ",
    description="Snapshot all admin virtual machines",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
)
def bundles_proxmox_configure_default_vms_stop_vms_admin(req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms):

    return bundles_proxmox_configure_default_vms_revert_snapshot(req, "core/proxmox/configure/default/vms/snapshot/revert-vms-admin" )

#######################################################################################################################

def bundles_proxmox_configure_default_vms_revert_snapshot(
        req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms,
        action_name: str) -> JSONResponse:

    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_filepath = utils.resolve_bundles_playbook(action_name, "public_github")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    extravars = request_checks(req)

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    payload = reply_processing(events, extravars, log_plain, rc, req)

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
                     req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms) -> dict[str, list | Any]:

    """ reply post-processing - json or ansible raw output """

    if req.as_json:  ##### OUTPUT TYPE - as_json=True

        # do not remove this. We want only vm_delete json line from the role (not the vm_stop_force !)
        extravars["proxmox_vm_action"] = "snapshot_vm_revert"
        #

        action = extravars["proxmox_vm_action"]
        result = extract_action_results(events, action)

        payload = {
            "rc": rc,
            "result": result
            # "action": action,
        }  # raw

    else:           #### OUTPUT AS TEXT - as_json=False

        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return payload



def request_checks(req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    
    if req.vm_snapshot_name is not None:
        extravars["VM_SNAPSHOT_NAME"] = req.vm_snapshot_name

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    # extravars["hosts"] = "proxmox"

    return extravars

