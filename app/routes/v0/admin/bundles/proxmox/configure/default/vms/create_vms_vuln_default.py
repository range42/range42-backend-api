from typing import Any, Dict
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.bundles.core.proxmox.configure.default.vms.create_vms_vuln_default import Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms
from app.schemas.bundles.core.proxmox.configure.default.vms.create_vms_vuln_default import Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms

from app.runner import run_playbook_core  # , extract_action_results
from app.extract_actions import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #30
#

debug = 1

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"


#
# => /v0/admin/run/bundles/core/proxmox/configure/vms/create-vms-vuln
#
@router.post(
    path="/core/proxmox/configure/default/create-vms-vuln",
    summary="Create default vulnerable VMs",
    description="Create the default set of vulnerable virtual machines for initial configuration in Proxmox",
    # tags=["bundles", "core", "proxmox", "default-configuration"]
    tags=["bundles - core - proxmox - vms - default-configuration - vuln"],
    response_model=Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms,
    # response_description="AAAAAAAAAAAAAAAAAA",
)
def bundles_proxmox_configure_default_vms_create_vms_vuln(
        req: Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms):
    action_name = "core/proxmox/configure/default/vms/create-vms-vuln"

    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    # checked_playbook_filepath  = utils.resolve_bundles_playbook(action_name, "public_github")
    checked_playbook_filepath = utils.resolve_bundles_playbook_init_file(action_name, "public_github")

    # results: list[Dict[str, Any]] = []

    if debug == 1:
        print(":: ACTION_NAME", action_name)
        print(":: REQUEST ::", req.model_dump())
        print(f":: PROJECT_ROOT :: {PROJECT_ROOT}")
        print(f":: INVENTORY   :: {checked_inventory_filepath}")
        print(f":: PLAYBOOK    :: {checked_playbook_filepath}")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    request_checks(req)

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    #
    # run init.yml - we must create vm first.
    #

    for vm_key, item in req.vms.items():

        extravars = {
            "proxmox_node": req.proxmox_node,
            "global_vm_id": item.vm_id,
            "global_vm_ci_ip": str(item.vm_ip),
            "global_vm_description": item.vm_description,
        }

        if debug == 1 :

            print()
            print(extravars["proxmox_node"])
            print("::: vm_key", vm_key, [vm_key])
            print("::: vm_de", extravars["global_vm_description"])
            print("::: vm_id", extravars["global_vm_id"])
            print("::: vm_ip", extravars["global_vm_ci_ip"])
            print()


        rc, events, log_plain, log_ansi = run_playbook_core(
            checked_playbook_filepath,
            checked_inventory_filepath,
            tags=vm_key,
            extravars=extravars,
            # limit=req.hosts,            # we use tags for init.yml !
        )

        # results.append({
        #     "proxmox_node": req.proxmox_node,
        #     "raw_data": (log_plain or "")
        # })

        if rc != 0:
            status = 500
            payload = reply_processing(log_plain, rc)
            return JSONResponse(payload, status_code=status)

    #
    # run main.yml
    #

    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_filepath = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {
        "proxmox_node": req.proxmox_node,
        # "global_vm_id": item.vm_id,
        # "global_vm_ci_ip": str(item.vm_ip),
        # "global_vm_description": item.vm_description,
    }

    # limit_admin = "r42-admin"

    # NOTE: on passe --tags=vm_key ; pas besoin de --limit ici
    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        extravars=extravars,
        # limit=limit_admin,            # useless - restricted in playbook
        # tags=vm_key,

    )

    # results.append({
    #     "proxmox_node": req.proxmox_node,
    #     "raw_data": (log_plain or "")
    # })

    payload = reply_processing(log_plain, rc)


    if rc == 0:
        status = 200
    else:
        status = 500

    return JSONResponse(payload, status_code=status)


def reply_processing(log_plain: str, rc ) -> dict[str, list[str] | Any]:
    """ reply post-processing - for ansible raw output """

    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return payload


def request_checks(req: Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    #####

    if not req.vms or len(req.vms) == 0:
        raise HTTPException(status_code=500, detail="Field vms must not be empty")

    allowed_vms = {
        "vuln-box-00",
        "vuln-box-01",
        "vuln-box-02",
        "vuln-box-03",
        "vuln-box-04",
    }

    for required in allowed_vms:
        if required not in req.vms:
            raise HTTPException(status_code=500, detail=f"Missing required vm key {required}")

    for vm in req.vms:
        if vm not in allowed_vms:
            raise HTTPException(status_code=500, detail=f"Unauthorized vm key {vm}" )

    #

    for vm_name, vm_spec in req.vms.items():

        if vm_spec.vm_id is None:
            raise HTTPException(status_code=500, detail=f"missing key vm_id for {vm_name}")

        if vm_spec.vm_description is None:
            raise HTTPException(status_code=500, detail=f"missing key vm_description for {vm_name}")

        if vm_spec.vm_ip is None:
            raise HTTPException(status_code=500, detail=f"missing key vm_ip for {vm_name}")

    # nothing :

    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    return extravars
