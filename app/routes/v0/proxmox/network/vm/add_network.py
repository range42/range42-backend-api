from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.proxmox.network.vm_id.add_network import Request_ProxmoxNetwork_WithVmId_AddNetwork
from app.schemas.proxmox.network.vm_id.add_network import Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results
# from app.utils.vm_id_name_resolver import resolv_id_to_vm_name
from app import utils
from pathlib import Path
import os

#
# ISSUE - #11
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[6]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"
# INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

#
# => /api/v0/admin/proxmox/network/node/add
#
@router.post(
    path="/vm/add",
    summary="Add VM network interface",
    description="Create and attach a new network interface to a Proxmox VM.",
    tags=["proxmox - network - vm"],
    response_model=Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface,
    response_description="Information about the added network interface.",
)
def proxmox_network_vm_add_interface(req: Request_ProxmoxNetwork_WithVmId_AddNetwork):

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
                     req: Request_ProxmoxNetwork_WithVmId_AddNetwork) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxNetwork_WithVmId_AddNetwork) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "network_add_interfaces_vm"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id

    ####

    if req.iface_model is not None:
        extravars["iface_model"] = req.iface_model

    if req.iface_bridge is not None:
        extravars["iface_bridge"] = req.iface_bridge
    #
    # if req.net_index is not None:
    #     extravars["net_index"] = req.net_index

    if req.vm_vmnet_id is not None:
        extravars["vm_vmnet_id"] = req.vm_vmnet_id

    #### fields to test later.

    if req.iface_trunks is not None:
        extravars["iface_trunks"] = req.iface_trunks

    if req.iface_tag is not None:
        extravars["iface_tag"] = req.iface_tag

    if req.iface_rate is not None:
        extravars["iface_rate"] = req.iface_rate

    if req.iface_queues is not None:
        extravars["iface_queues"] = req.iface_queues

    if req.iface_mtu is not None:
        extravars["iface_mtu"] = req.iface_mtu

    if req.iface_macaddr is not None:
        extravars["iface_macaddr"] = req.iface_macaddr

    if req.iface_link_down is not None:
        extravars["iface_link_down"] = req.iface_link_down

    if req.iface_firewall is not None:
        extravars["iface_firewall"] = req.iface_firewall

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    extravars["hosts"] = "proxmox"
    return extravars

