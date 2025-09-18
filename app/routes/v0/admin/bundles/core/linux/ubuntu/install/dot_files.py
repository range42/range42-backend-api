from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.bundles.core.linux.ubuntu.install.dot_files import Request_BundlesCoreLinuxUbuntuInstall_DotFiles
from app.schemas.bundles.core.linux.ubuntu.install.dot_files import Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #28
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

@router.post(
    path="/core/linux/ubuntu/install/dot-files",
    summary="Install user dotfiles",
    description="Install and configure generic dotfiles - vimrc, zshrc, etc.",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem,
    # response_description="AAAAAAAAAAAAAAAAAA",
)
def bundles_core_linux_ubuntu_install_dotfiles_router( req: Request_BundlesCoreLinuxUbuntuInstall_DotFiles):

    action_name = "core/linux/ubuntu/install/dot-files"
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


def request_checks(req: Request_BundlesCoreLinuxUbuntuInstall_DotFiles) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.hosts is not  None :
        extravars["hosts"] = req.hosts

    #

    if req.user is not None:
        extravars["OPERATOR_USER"] = req.user

    if req.install_vim_dot_files is not None:
        extravars["INSTALL_VIM_DOTFILES"] = req.install_vim_dot_files

    if req.install_zsh_dot_files is not None:
        extravars["INSTALL_ZSH_DOTFILES"] = req.install_zsh_dot_files

    if req.apply_for_root is not  None :
        extravars["APPLY_FOR_ROOT"] = req.apply_for_root

    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")


    # extravars["hosts"] = "proxmox"

    return extravars

