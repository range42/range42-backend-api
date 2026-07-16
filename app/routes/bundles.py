"""Consolidated bundle routes.

Endpoints
---------
Ubuntu install/configure bundles:

- ``POST .../core/linux/ubuntu/install/docker`` -- Install Docker.
- ``POST .../core/linux/ubuntu/install/docker-compose`` -- Install Docker Compose.
- ``POST .../core/linux/ubuntu/install/basic-packages`` -- Install basic packages.
- ``POST .../core/linux/ubuntu/install/dot-files`` -- Install dotfiles.
- ``POST .../core/linux/ubuntu/configure/add-user`` -- Add a system user.

All prefixed under ``/v0/admin/run/bundles``. The HTTP paths above are kept for
API compatibility; the resolved bundle paths follow the generic/ tier grammar
(range42-playbooks#133).

The former Proxmox default-VM bundle routes were removed: their bundles are
retired to ``decom/`` in range42-playbooks#133 and had no live caller.
"""

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import utils
from app.core.logging import get_logger
from app.core.runner import run_playbook_core

# --- Linux/Ubuntu bundle schemas ---
from app.schemas.bundles import (
    Reply_BundlesCoreLinuxUbuntuConfigure_AddUser,
    Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages,
    Reply_BundlesCoreLinuxUbuntuInstall_Docker,
    Reply_BundlesCoreLinuxUbuntuInstall_DockerCompose,
    Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem,
    Request_BundlesCoreLinuxUbuntuConfigure_AddUser,
    Request_BundlesCoreLinuxUbuntuInstall_BasicPackages,
    Request_BundlesCoreLinuxUbuntuInstall_Docker,
    Request_BundlesCoreLinuxUbuntuInstall_DockerCompose,
    Request_BundlesCoreLinuxUbuntuInstall_DotFiles,
)

logger = get_logger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

router = APIRouter()


# ---------------------------------------------------------------------------
# Pattern B helpers: simple playbook run (Ubuntu bundles)
# ---------------------------------------------------------------------------


def _run_bundle_simple(req, action_name: str, extravars: dict) -> JSONResponse:
    """Run a single bundle playbook (Pattern B)."""
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook = utils.resolve_bundles_playbook(action_name, "public_github")

    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook,
        checked_inventory,
        limit=req.hosts,
        extravars=extravars,
    )

    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


# ===========================================================================
# Linux/Ubuntu install bundles
# ===========================================================================


@router.post(
    path="/core/linux/ubuntu/install/docker",
    summary="Install docker packages",
    description="Install and configure docker engine on the target ubuntu system",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_Docker,
)
def bundles_core_linux_ubuntu_install_docker(
    req: Request_BundlesCoreLinuxUbuntuInstall_Docker,
):
    """Install and configure Docker on the target Ubuntu system.

    :param req: Request body with host, node, and package installation flags.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.install_package_docker is not None:
        extravars["INSTALL_PACKAGES_DOCKER"] = req.install_package_docker
    if req.install_ntpclient_and_update_time is not None:
        extravars["INSTALL_PACKAGES_NTP_AND_UPDATE_TIME"] = (
            req.install_ntpclient_and_update_time
        )
    if req.packages_cleaning is not None:
        extravars["SPECIFIC_PACKAGES_CLEANING"] = req.packages_cleaning
    return _run_bundle_simple(
        req, "generic/software.install.docker", extravars or None
    )


@router.post(
    path="/core/linux/ubuntu/install/docker-compose",
    summary="Install docker compose packages",
    description="Install and configure docker compose on the target ubuntu system",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_DockerCompose,
)
def bundles_core_linux_ubuntu_install_docker_compose(
    req: Request_BundlesCoreLinuxUbuntuInstall_DockerCompose,
):
    """Install and configure Docker Compose on the target Ubuntu system.

    :param req: Request body with host, node, and package installation flags.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.install_package_docker is not None:
        extravars["INSTALL_PACKAGES_DOCKER"] = req.install_package_docker
    if req.install_package_docker_compose is not None:
        extravars["INSTALL_PACKAGES_DOCKER_COMPOSE"] = (
            req.install_package_docker_compose
        )
    if req.install_ntpclient_and_update_time is not None:
        extravars["INSTALL_PACKAGES_NTP_AND_UPDATE_TIME"] = (
            req.install_ntpclient_and_update_time
        )
    if req.packages_cleaning is not None:
        extravars["SPECIFIC_PACKAGES_CLEANING"] = req.packages_cleaning
    return _run_bundle_simple(
        req, "generic/software.install.docker_compose", extravars or None
    )


@router.post(
    path="/core/linux/ubuntu/install/basic-packages",
    summary="Install basics packages",
    description="Install and configure a base set of packages on the target Ubuntu system",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages,
)
def bundles_core_linux_ubuntu_install_basic_packages(
    req: Request_BundlesCoreLinuxUbuntuInstall_BasicPackages,
):
    """Install a base set of packages on the target Ubuntu system.

    :param req: Request body with host, node, and package category flags.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    for field, key in [
        ("install_package_basics", "INSTALL_PACKAGES_BASICS"),
        ("install_package_firewalls", "INSTALL_PACKAGES_FIREWALLS"),
        ("install_package_docker", "INSTALL_PACKAGES_DOCKER"),
        ("install_package_docker_compose", "INSTALL_PACKAGES_DOCKER_COMPOSE"),
        ("install_package_utils_json", "INSTALL_PACKAGES_UTILS_JSON"),
        ("install_package_utils_network", "INSTALL_PACKAGES_UTILS_NETWORK"),
        ("install_ntpclient_and_update_time", "INSTALL_PACKAGES_NTP_AND_UPDATE_TIME"),
        ("packages_cleaning", "SPECIFIC_PACKAGES_CLEANING"),
    ]:
        val = getattr(req, field, None)
        if val is not None:
            extravars[key] = val
    return _run_bundle_simple(
        req, "generic/software.install.basic_packages", extravars or None
    )


@router.post(
    path="/core/linux/ubuntu/install/dot-files",
    summary="Install user dotfiles",
    description="Install and configure generic dotfiles - vimrc, zshrc, etc.",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem,
)
def bundles_core_linux_ubuntu_install_dotfiles(
    req: Request_BundlesCoreLinuxUbuntuInstall_DotFiles,
):
    """Install generic dotfiles (vimrc, zshrc, etc.) for a user.

    :param req: Request body with host, user, and dotfile selection flags.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.hosts is not None:
        extravars["hosts"] = req.hosts
    if req.user is not None:
        extravars["OPERATOR_USER"] = req.user
    if req.install_vim_dot_files is not None:
        extravars["INSTALL_VIM_DOTFILES"] = req.install_vim_dot_files
    if req.install_zsh_dot_files is not None:
        extravars["INSTALL_ZSH_DOTFILES"] = req.install_zsh_dot_files
    if req.apply_for_root is not None:
        extravars["APPLY_FOR_ROOT"] = req.apply_for_root
    return _run_bundle_simple(
        req, "generic/software.install.dotfiles", extravars or None
    )


# ===========================================================================
# Linux/Ubuntu configure bundles
# ===========================================================================


@router.post(
    path="/core/linux/ubuntu/configure/add-user",
    summary="Add system user",
    description="Create a new user with shell, home and password",
    tags=["bundles - core - ubuntu "],
    response_model=Reply_BundlesCoreLinuxUbuntuConfigure_AddUser,
)
def bundles_core_linux_ubuntu_configure_add_user(
    req: Request_BundlesCoreLinuxUbuntuConfigure_AddUser,
):
    """Create a new system user with shell, home directory, and password.

    :param req: Request body with host, user details, and password policy.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if req.user is not None:
        extravars["TARGET_USER"] = req.user
    if req.password is not None:
        extravars["TARGET_PASSWORD"] = req.password
    if req.shell_path is not None:
        extravars["TARGET_SHELL_PATH"] = req.shell_path
    if req.change_pwd_at_logon is not None:
        extravars["CHANGE_PWD_AT_LOGON"] = req.change_pwd_at_logon
    return _run_bundle_simple(
        req, "generic/credentials.create.user", extravars or None
    )
