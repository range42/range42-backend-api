from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.proxmox.firewall.apply_iptables_rules import Request_ProxmoxFirewall_ApplyIptablesRules
from app.schemas.proxmox.firewall.apply_iptables_rules import Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #12
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
# => /api/proxmox/firewall/vm/rules/apply ---- should rename to rules/add ?
#
@router.post(
    path="/vm/rules/apply",
    summary="Apply firewall rules",
    description="Apply the received firewall rules to the proxmox firewall",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules,
    response_description="Details of the applied firewall rules",
)
def proxmox_firewall_vm_rules_add(req: Request_ProxmoxFirewall_ApplyIptablesRules):

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
                     req: Request_ProxmoxFirewall_ApplyIptablesRules) -> dict[str, list | Any]:

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


def request_checks(req: Request_ProxmoxFirewall_ApplyIptablesRules) -> dict[Any, Any]:
    """ request checks """

    extravars = {}
    extravars["proxmox_vm_action"] = "firewall_vm_apply_iptables_rule"

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id

    ####

    if req.vm_fw_action is not None:
        extravars["vm_fw_action"] = req.vm_fw_action

    if req.vm_fw_dport is not None:
        extravars["vm_fw_dport"] = req.vm_fw_dport

    if req.vm_fw_enable is not None:
        extravars["vm_fw_enable"] = req.vm_fw_enable

    if req.vm_fw_proto is not None:
        extravars["vm_fw_proto"] = req.vm_fw_proto

    if req.vm_fw_type is not None:
        extravars["vm_fw_type"] = req.vm_fw_type

    if req.vm_fw_log is not None:
        extravars["vm_fw_log"] = req.vm_fw_log

    if req.vm_fw_iface is not None:
        extravars["vm_fw_iface"] = req.vm_fw_iface

    if req.vm_fw_source is not None:
        extravars["vm_fw_source"] = req.vm_fw_source

    if req.vm_fw_dest is not None:
        extravars["vm_fw_dest"] = req.vm_fw_dest

    if req.vm_fw_sport is not None:
        extravars["vm_fw_sport"] = req.vm_fw_sport

    if req.vm_fw_comment is not None:
        extravars["vm_fw_comment"] = req.vm_fw_comment

    if req.vm_fw_pos is not None:
        extravars["vm_fw_pos"] = req.vm_fw_pos

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")

    extravars["hosts"] = "proxmox"
    return extravars

