from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.bundles.core.linux.ubuntu.configure.add_user import Request_BundlesCoreLinuxUbuntuConfigure_AddUser
from app.schemas.bundles.core.linux.ubuntu.configure.add_user import Reply_BundlesCoreLinuxUbuntuConfigure_AddUser

from app.runner import  run_playbook_core # , extract_action_results
from app.extract_actions import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #19
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

#
# => /v0/admin/run/actions/core/linux/ubuntu/configure/add-user
#
@router.post(
    path="/core/linux/ubuntu/configure/add-user",
    summary="Add system user",
    description="Create a new user with shell, home and password",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuConfigure_AddUser,
    # response_description="AAAAAAAAAAAAAAAAAA",
)
def bundles_core_linux_ubuntu_install_dotfiles_router( req: Request_BundlesCoreLinuxUbuntuConfigure_AddUser):

    action_name = "core/linux/ubuntu/configure/add-user"
    #
    checked_inventory_filepath = utils.resolve_inventory(INVENTORY_NAME)
    # checked_playbook_filepath  = utils.resolve_actions_playbook(action_name, "public_github")
    checked_playbook_filepath  = utils.resolve_bundles_playbook(action_name, "public_github")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    if debug ==1:
        print("::  ACTION_NAME", action_name)
        print("::  REQUEST ::", req.model_dump())
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


def request_checks(req: Request_BundlesCoreLinuxUbuntuConfigure_AddUser) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    # moved to --limit switch
    # if req.hosts is not None:
    #     extravars["hosts"] = req.hosts

    #####

    if req.user is not None:
        extravars["TARGET_USER"] = req.user

    if req.password is not None:
        extravars["TARGET_PASSWORD"] = req.password

    if req.shell_path is not None:
        extravars["TARGET_SHELL_PATH"] = req.shell_path

    if req.change_pwd_at_logon is not None:
        extravars["CHANGE_PWD_AT_LOGON"] = req.change_pwd_at_logon

    # nothing :

    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")


    # extravars["hosts"] = "proxmox"

    return extravars



