"""Consolidated bundle routes.

Endpoints
---------
Ubuntu install bundles:

- ``POST .../core/linux/ubuntu/install/docker`` -- Install Docker.
- ``POST .../core/linux/ubuntu/install/docker-compose`` -- Install Docker Compose.
- ``POST .../core/linux/ubuntu/install/basic-packages`` -- Install basic packages.
- ``POST .../core/linux/ubuntu/install/dot-files`` -- Install dotfiles.
- ``POST .../core/linux/ubuntu/configure/add-user`` -- Add a system user.

Proxmox VM bundles (create/start/stop/pause/resume/delete/snapshot):

- ``POST .../core/proxmox/configure/default/create-vms-admin``
- ``POST .../core/proxmox/configure/default/create-vms-vuln``
- ``POST .../core/proxmox/configure/default/create-vms-student``
- ``POST .../core/proxmox/configure/default/{action}-vms-{role}``
- ``DELETE .../core/proxmox/configure/default/delete-vms-{role}``
- ``POST .../core/proxmox/configure/default/snapshot/create-vms-{role}``
- ``POST .../core/proxmox/configure/default/snapshot/revert-vms-{role}``

All prefixed under ``/v0/admin/run/bundles``.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import utils
from app.core.extractor import extract_action_results
from app.core.runner import run_playbook_core

# --- Linux/Ubuntu bundle schemas ---
from app.schemas.bundles import (
    Reply_BundlesCoreLinuxUbuntuConfigure_AddUser,
    Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages,
    Reply_BundlesCoreLinuxUbuntuInstall_Docker,
    Reply_BundlesCoreLinuxUbuntuInstall_DockerCompose,
    Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem,
    Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms,
    Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms,
    Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms,
    Request_BundlesCoreLinuxUbuntuConfigure_AddUser,
    Request_BundlesCoreLinuxUbuntuInstall_BasicPackages,
    Request_BundlesCoreLinuxUbuntuInstall_Docker,
    Request_BundlesCoreLinuxUbuntuInstall_DockerCompose,
    Request_BundlesCoreLinuxUbuntuInstall_DotFiles,
    # --- Proxmox bundle schemas ---
    Request_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms,
    Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms,
    Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms,
    Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms,
    Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
)
from app.schemas.snapshots import (
    Reply_ProxmoxVmsVMID_CreateSnapshot,
    Reply_ProxmoxVmsVMID_RevertSnapshot,
)
from app.schemas.vms import Reply_ProxmoxVmsVMID_StartStopPauseResume

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Pattern C helpers: multi-step Proxmox bundles
# ---------------------------------------------------------------------------


def _run_create_vms_bundle(req, action_name: str, request_checks_fn) -> JSONResponse:
    """Multi-step create VMs: init.yml per VM, then main.yml (Pattern C)."""
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook_init = utils.resolve_bundles_playbook_init_file(
        action_name, "public_github"
    )

    request_checks_fn(req)

    # Phase 1: run init.yml for each VM
    for vm_key, item in req.vms.items():
        extravars = {
            "proxmox_node": req.proxmox_node,
            "global_vm_id": item.vm_id,
            "global_vm_ci_ip": str(item.vm_ip),
            "global_vm_description": item.vm_description,
        }
        rc, events, log_plain, _ = run_playbook_core(
            checked_playbook_init,
            checked_inventory,
            tags=vm_key,
            extravars=extravars,
        )
        if rc != 0:
            payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
            return JSONResponse(payload, status_code=500)

    # Phase 2: run main.yml
    checked_playbook_main = utils.resolve_bundles_playbook(action_name, "public_github")
    extravars = {"proxmox_node": req.proxmox_node}
    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook_main,
        checked_inventory,
        extravars=extravars,
    )
    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


def _run_start_stop_bundle(
    req, action_name: str, proxmox_vm_action: str
) -> JSONResponse:
    """Start/stop/pause/resume bundle (Pattern C variant)."""
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {"proxmox_vm_action": proxmox_vm_action}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook,
        checked_inventory,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    if req.as_json:
        result = extract_action_results(events, proxmox_vm_action)
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


def _run_delete_bundle(req, action_name: str) -> JSONResponse:
    """Delete VMs bundle."""
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node

    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook,
        checked_inventory,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    if req.as_json:
        extravars["proxmox_vm_action"] = "vm_delete"
        result = extract_action_results(events, "vm_delete")
        payload = {"rc": rc, "result": result}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}

    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


def _run_snapshot_bundle(req, action_name: str, action_key: str) -> JSONResponse:
    """Snapshot create/revert bundle."""
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook = utils.resolve_bundles_playbook(action_name, "public_github")

    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if hasattr(req, "vm_snapshot_name") and req.vm_snapshot_name is not None:
        extravars["VM_SNAPSHOT_NAME"] = req.vm_snapshot_name

    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook,
        checked_inventory,
        limit=req.proxmox_node,
        extravars=extravars,
    )

    if req.as_json:
        extravars["proxmox_vm_action"] = action_key
        result = extract_action_results(events, action_key)
        payload = {"rc": rc, "result": result}
    else:
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
        req, "core/linux/ubuntu/install/docker", extravars or None
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
        req, "core/linux/ubuntu/install/docker", extravars or None
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
        req, "core/linux/ubuntu/install/basic-packages", extravars or None
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
        req, "core/linux/ubuntu/install/dot-files", extravars or None
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
        req, "core/linux/ubuntu/configure/add-user", extravars or None
    )


# ===========================================================================
# Proxmox create VMs bundles (Pattern C)
# ===========================================================================


def _check_admin_vms(req):
    if not req.vms or len(req.vms) == 0:
        raise HTTPException(status_code=400, detail="Field vms must not be empty")
    allowed = {
        "admin-wazuh",
        "admin-web-api-kong",
        "admin-web-builder-api",
        "admin-web-deployer-ui",
        "admin-web-emp",
    }
    for r in allowed:
        if r not in req.vms:
            raise HTTPException(status_code=400, detail=f"Missing required vm key {r}")
    for vm in req.vms:
        if vm not in allowed:
            raise HTTPException(status_code=400, detail=f"Unauthorized vm key {vm}")
    for vm_name, vm_spec in req.vms.items():
        if vm_spec.vm_id is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_id for {vm_name}"
            )
        if vm_spec.vm_description is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_description for {vm_name}"
            )
        if vm_spec.vm_ip is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_ip for {vm_name}"
            )


def _check_vuln_vms(req):
    if not req.vms or len(req.vms) == 0:
        raise HTTPException(status_code=500, detail="Field vms must not be empty")
    allowed = {
        "vuln-box-00",
        "vuln-box-01",
        "vuln-box-02",
        "vuln-box-03",
        "vuln-box-04",
    }
    for r in allowed:
        if r not in req.vms:
            raise HTTPException(status_code=500, detail=f"Missing required vm key {r}")
    for vm in req.vms:
        if vm not in allowed:
            raise HTTPException(status_code=500, detail=f"Unauthorized vm key {vm}")
    for vm_name, vm_spec in req.vms.items():
        if vm_spec.vm_id is None:
            raise HTTPException(
                status_code=500, detail=f"missing key vm_id for {vm_name}"
            )
        if vm_spec.vm_description is None:
            raise HTTPException(
                status_code=500, detail=f"missing key vm_description for {vm_name}"
            )
        if vm_spec.vm_ip is None:
            raise HTTPException(
                status_code=500, detail=f"missing key vm_ip for {vm_name}"
            )


def _check_student_vms(req):
    if not req.vms or len(req.vms) == 0:
        raise HTTPException(status_code=400, detail="Field vms must not be empty")
    allowed = {"student-box-01"}
    for r in allowed:
        if r not in req.vms:
            raise HTTPException(status_code=400, detail=f"Missing required vm key {r}")
    for vm in req.vms:
        if vm not in allowed:
            raise HTTPException(status_code=400, detail=f"Unauthorized vm key {vm}")
    for vm_name, vm_spec in req.vms.items():
        if vm_spec.vm_id is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_id for {vm_name}"
            )
        if vm_spec.vm_description is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_description for {vm_name}"
            )
        if vm_spec.vm_ip is None:
            raise HTTPException(
                status_code=400, detail=f"missing key vm_ip for {vm_name}"
            )


@router.post(
    path="/core/proxmox/configure/default/create-vms-admin",
    summary="Create default admin VMs",
    description="Create the default set of admin virtual machines for initial configuration in Proxmox",
    tags=["bundles - core - proxmox - vms - default-configuration - admin"],
    response_model=Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms,
)
def bundles_proxmox_create_vms_admin(
    req: Request_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms,
):
    """Create the default set of admin VMs on Proxmox.

    :param req: Request body with ``proxmox_node`` and ``vms`` dict.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    return _run_create_vms_bundle(
        req, "core/proxmox/configure/default/vms/create-vms-admin", _check_admin_vms
    )


@router.post(
    path="/core/proxmox/configure/default/create-vms-vuln",
    summary="Create default vulnerable VMs",
    description="Create the default set of vulnerable virtual machines for initial configuration in Proxmox",
    tags=["bundles - core - proxmox - vms - default-configuration - vuln"],
    response_model=Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms,
)
def bundles_proxmox_create_vms_vuln(
    req: Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms,
):
    """Create the default set of vulnerable VMs on Proxmox.

    :param req: Request body with ``proxmox_node`` and ``vms`` dict.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    return _run_create_vms_bundle(
        req, "core/proxmox/configure/default/vms/create-vms-vuln", _check_vuln_vms
    )


@router.post(
    path="/core/proxmox/configure/default/create-vms-student",
    summary="Create default student VMs",
    description="Create the default set of student virtual machines for initial configuration in Proxmox",
    tags=["bundles - core - proxmox - vms - default-configuration - student"],
    response_model=Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms,
)
def bundles_proxmox_create_vms_student(
    req: Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms,
):
    """Create the default set of student VMs on Proxmox.

    :param req: Request body with ``proxmox_node`` and ``vms`` dict.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    return _run_create_vms_bundle(
        req, "core/proxmox/configure/default/vms/create-vms-student", _check_student_vms
    )


# ===========================================================================
# Proxmox start/stop/pause/resume bundles for admin, vuln, student
# ===========================================================================

_SSPR = {
    "admin": "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-admin",
    "vuln": "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-vuln",
    "student": "core/proxmox/configure/default/vms/start-stop-pause-resume-vms-student",
}

for _role in ("admin", "vuln", "student"):
    for _action, _verb in [
        ("start", "vm_start"),
        ("stop", "vm_stop"),
        ("pause", "vm_pause"),
        ("resume", "vm_resume"),
    ]:
        _path = f"/core/proxmox/configure/default/{_action}-vms-{_role}"
        _tag = f"bundles - core - proxmox - vms - default-configuration - {_role}"
        _action_name = _SSPR[_role]

        def _make_handler(_an=_action_name, _v=_verb):
            def handler(
                req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
            ):
                return _run_start_stop_bundle(req, _an, _v)

            return handler

        router.add_api_route(
            _path,
            _make_handler(),
            methods=["POST"],
            summary=f"{_action.capitalize()} {_role} vms ",
            description=f"{_action.capitalize()} all {_role} virtual machines",
            tags=[_tag],
            response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
        )


# ===========================================================================
# Proxmox delete VMs bundles
# ===========================================================================

_DELETE_ACTIONS = {
    "student": "core/proxmox/configure/default/vms/delete-vms-student",
    "vuln": "core/proxmox/configure/default/vms/delete-vms-vuln",
    "admin": "core/proxmox/configure/default/vms/delete-vms-admin",
}

for _role, _an in _DELETE_ACTIONS.items():
    _path = f"/core/proxmox/configure/default/delete-vms-{_role}"
    _tag = f"bundles - core - proxmox - vms - default-configuration - {_role}"

    def _make_delete_handler(_an=_an):
        def handler(
            req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
        ):
            return _run_delete_bundle(req, _an)

        return handler

    router.add_api_route(
        _path,
        _make_delete_handler(),
        methods=["DELETE"],
        summary=f"Delete {_role} vms ",
        description=f"Delete all {_role} virtual machines",
        tags=[_tag],
        response_model=Reply_ProxmoxVmsVMID_StartStopPauseResume,
    )


# ===========================================================================
# Proxmox snapshot create bundles
# ===========================================================================

_SNAP_CREATE = {
    "student": "core/proxmox/configure/default/vms/snapshot/create-vms-student",
    "vuln": "core/proxmox/configure/default/vms/snapshot/create-vms-vuln",
    "admin": "core/proxmox/configure/default/vms/snapshot/create-vms-admin",
}

for _role, _an in _SNAP_CREATE.items():
    _path = f"/core/proxmox/configure/default/snapshot/create-vms-{_role}"
    _tag = f"bundles - core - proxmox - vms - default-configuration - {_role}"

    def _make_snap_create_handler(_an=_an):
        def handler(
            req: Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms,
        ):
            return _run_snapshot_bundle(req, _an, "snapshot_vm_create")

        return handler

    router.add_api_route(
        _path,
        _make_snap_create_handler(),
        methods=["POST"],
        summary=f"Snapshot {_role} vms ",
        description=f"Snapshot all {_role} virtual machines",
        tags=[_tag],
        response_model=Reply_ProxmoxVmsVMID_CreateSnapshot,
    )


# ===========================================================================
# Proxmox snapshot revert bundles
# ===========================================================================

_SNAP_REVERT = {
    "student": "core/proxmox/configure/default/vms/snapshot/revert-vms-student",
    "vuln": "core/proxmox/configure/default/vms/snapshot/revert-vms-vuln",
    "admin": "core/proxmox/configure/default/vms/snapshot/revert-vms-admin",
}

for _role, _an in _SNAP_REVERT.items():
    _path = f"/core/proxmox/configure/default/snapshot/revert-vms-{_role}"
    _tag = f"bundles - core - proxmox - vms - default-configuration - {_role}"

    def _make_snap_revert_handler(_an=_an):
        def handler(
            req: Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms,
        ):
            return _run_snapshot_bundle(req, _an, "snapshot_vm_revert")

        return handler

    router.add_api_route(
        _path,
        _make_snap_revert_handler(),
        methods=["POST"],
        summary=f"Snapshot {_role} vms ",
        description=f"Snapshot all {_role} virtual machines",
        tags=[_tag],
        response_model=Reply_ProxmoxVmsVMID_RevertSnapshot,
    )
