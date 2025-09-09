
from pathlib import Path
import os

from typing import Any
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.runner import  run_playbook_core
from app.schemas.debug.ping import Request_DebugPing

from app import utils

#
# ISSUE - #15
#

router = APIRouter()

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

# PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "ping.yml"
# PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
# INVENTORY_NAME =  "hosts"

debug = 1

@router.post(
    path="/{action_name}/run",
    summary="Run action",
    description="Run generic action with default (and static) extras_vars ",
    tags=["runner"],
)

def debug_ping(action_name: str, req: Request_DebugPing):

    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_filepath  = utils.resolve_actions_playbook(action_name, "public_github")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    if debug ==1:
        print("::  ACTION_NAME", action_name)
        print("::  REQUEST ::", req.dict())
        print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
        print(f":: checked_inventory_filepath :: {checked_inventory_filepath} ")
        print(f":: checked_playbook_filepath  :: {checked_playbook_filepath} ")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    extravars = request_checks(req)

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        checked_playbook_filepath,
        checked_inventory_filepath,
        limit=req.hosts,
        extravars=extravars,
    )

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    payload = reply_processing(log_plain, rc)

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    if rc == 0:
        status = 200
    else:
        status = 500

    return JSONResponse(payload, status_code=status)


def reply_processing(log_plain: str,
                     rc
                     ) -> dict[str, list[str] | Any]:

    """ reply post-processing - for ansible raw output """

    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return payload


def request_checks(req: Request_DebugPing) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")


    # extravars["hosts"] = "proxmox"

    return extravars

