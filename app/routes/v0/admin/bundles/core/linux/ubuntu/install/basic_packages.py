from typing import Any
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.bundles.core.linux.ubuntu.install.basic_packages import Request_BundlesCoreLinuxUbuntuInstall_BasicPackages
from app.schemas.bundles.core.linux.ubuntu.install.basic_packages import Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results
from app import utils
from pathlib import Path
import os

#
# ISSUE - #20
#

debug = 0

router = APIRouter()
logger = logging.getLogger(__name__)

# PROJECT_ROOT = Path(__file__).resolve().parents[5]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

#
# => /v0/admin/run/actions/core/linux/ubuntu/install/basic-packages
#
@router.post(
    path="/core/linux/ubuntu/install/basic-packages",
    summary="Install basics packages",
    description="Install and configure a base set of packages on the target Ubuntu system",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages,
    # response_description="AAAAAAAAAAAAAAAAAA",
)
def bundles_core_linux_ubuntu_install_dotfiles_router( req: Request_BundlesCoreLinuxUbuntuInstall_BasicPackages):

    action_name = "core/linux/ubuntu/install/basic-packages"
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


def request_checks(req: Request_BundlesCoreLinuxUbuntuInstall_BasicPackages) -> dict[Any, Any]:
    """ request checks """

    extravars = {}

    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    if req.hosts is not  None :
        extravars["hosts"] = req.hosts

    #

    if req.install_package_basics is not None:
        extravars["INSTALL_PACKAGES_BASICS"] = req.install_package_basics

    if req.install_package_firewalls is not None:
        extravars["INSTALL_PACKAGES_FIREWALLS"] = req.install_package_firewalls

    if req.install_package_docker is not None:
        extravars["INSTALL_PACKAGES_DOCKER"] = req.install_package_docker

    if req.install_package_docker_compose is not None:
        extravars["INSTALL_PACKAGES_DOCKER_COMPOSE"] = req.install_package_docker_compose

    if req.install_package_utils_json is not None:
        extravars["INSTALL_PACKAGES_UTILS_JSON"] = req.install_package_utils_json

    if req.install_package_utils_network is not None:
        extravars["INSTALL_PACKAGES_UTILS_NETWORK"] = req.install_package_utils_network

    if req.install_ntpclient_and_update_time is not None:
        extravars["INSTALL_PACKAGES_NTP_AND_UPDATE_TIME"] = req.install_ntpclient_and_update_time

    if req.packages_cleaning is not None:
        extravars["SPECIFIC_PACKAGES_CLEANING"] = req.packages_cleaning


    # nothing :
    if not extravars:
        extravars = None

    if debug == 1:
        print(f", extra vars : {extravars}")


    # extravars["hosts"] = "proxmox"

    return extravars

