"""Consolidated bundle schemas: Ubuntu packages/configure + Proxmox default VM ops.

This __init__.py serves double duty:
1. Makes bundles/ a proper Python package so old imports (app.schemas.bundles.core...) keep working.
2. Exposes consolidated schema classes for new code to import from app.schemas.bundles.
"""

from typing import Dict, List, Literal
from pydantic import BaseModel, Field


# ===========================================================================
# Ubuntu Bundles
# ===========================================================================

# ---------------------------------------------------------------------------
# Add User
# ---------------------------------------------------------------------------

class BundleAddUserRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"
    )

    ####

    user: str = Field(
        ...,
        description = "New user",
        pattern = r"^[a-z_][a-z0-9_-]*$",
    )


    password: str = Field(
        ...,
        description = "New password",
        pattern = r"^[A-Za-z0-9@._-]*$"  # dangerous chars removed.
    )


    change_pwd_at_logon : bool = Field(
        ...,
        description = "Force user to change password on first login"
    )

    shell_path: str = Field(
        ...,
        description = "Default user shell ",
        pattern = r"^/[a-z/]*$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "user": "elliot",
                "password": "r0b0t_aLd3rs0n",
                "change_pwd_at_logon": False,
                "shell_path": "/bin/sh",
            }
        }
    }


class BundleAddUserItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleAddUserReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleAddUserItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Install Basic Packages
# ---------------------------------------------------------------------------

class BundleBasicPackagesRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"
    )

    ####

    install_package_basics : bool =  Field(
    ...,
    description="",
    )

    install_package_firewalls : bool =  Field(
    ...,
    description="",
    )

    install_package_docker : bool =  Field(
    ...,
    description="",
    )

    install_package_docker_compose: bool =  Field(
    ...,
    description="",
    )

    install_package_utils_json : bool =  Field(
    ...,
    description="",
    )

    install_package_utils_network : bool =  Field(
    ...,
    description="",
    )

    ####

    install_ntpclient_and_update_time: bool =  Field(
    ...,
        description="",
    )

    packages_cleaning: bool =  Field(
    ...,
        description="",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_basics" : True,
                "install_package_firewalls"  : False,
                "install_package_docker"  : False,
                "install_package_docker_compose" : False,
                "install_package_utils_json"  : False,
                "install_package_utils_network" : False,
                "install_ntpclient_and_update_time" : True,
                "packages_cleaning" : True,

            }
        }
    }


class BundleBasicPackagesItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleBasicPackagesReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleBasicPackagesItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Install Docker
# ---------------------------------------------------------------------------

class BundleDockerRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"
    )

    ####

    install_package_docker : bool =  Field(
    ...,
    description="",
    )

    ####

    install_ntpclient_and_update_time: bool =  Field(
    ...,
        description="",
    )

    packages_cleaning: bool =  Field(
    ...,
        description="",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_docker"  : True,
                "install_ntpclient_and_update_time" : True,
                "packages_cleaning" : True,

            }
        }
    }


class BundleDockerItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleDockerReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDockerItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Install Docker Compose
# ---------------------------------------------------------------------------

class BundleDockerComposeRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"

    )

    ####

    install_package_docker : bool =  Field(
    ...,
    description="",
    )

    install_package_docker_compose: bool =  Field(
    ...,
    description="",
    )

    ####

    install_ntpclient_and_update_time: bool =  Field(
    ...,
        description="",
    )

    packages_cleaning: bool =  Field(
    ...,
        description="",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_docker"  : True,
                "install_package_docker_compose" : True,
                "install_ntpclient_and_update_time" : True,
                "packages_cleaning" : True,

            }
        }
    }


class BundleDockerComposeItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleDockerComposeReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDockerComposeItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Install Dot Files
# ---------------------------------------------------------------------------

class BundleDotFilesRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"
    )

    ####

    user: str = Field(
        ...,
        description="targeted username",

    )

    install_vim_dot_files: bool = Field(
        ...,
        description= "Install vim dot file in user directory"
    )

    install_zsh_dot_files: bool = Field(
        ...,
        description= "Install zsh dot file in user directory"
    )

    apply_for_root: bool = Field(
        ...,
        description= "Install dot files in /root"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "user": "jane",
                "install_vim_dot_files": True,
                "install_zsh_dot_files": True,
                "apply_for_root": False,
            }
        }
    }


class BundleDotFilesItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


# NOTE: The original file had a bug where the Reply class was also named
# Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem (same as Item reply).
# We preserve both the Item and the Reply under their correct new names.
class BundleDotFilesReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDotFilesItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ===========================================================================
# Proxmox Bundles -- Default VM Operations
# ===========================================================================

# ---------------------------------------------------------------------------
# Create Admin VMs (Default)
# ---------------------------------------------------------------------------

class BundleCreateAdminVmsItemRequest(BaseModel):

    vm_id: int = Field(
        ...,
        ge=1,
        description="Virtual machine id",
    )

    vm_ip: str = Field(
        ...,
        description="vm ipv4",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    vm_description: str = Field(
        ...,
        strip_whitespace=True,
        max_length=200,
        # pattern=VM_DESCRIPTION_RE,
        description="Description"
    )

class BundleCreateAdminVmsRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vms: Dict[str, BundleCreateAdminVmsItemRequest] = Field(
        ...,
        description="Map <ssh_hostname> - vm override vm_id vm_ip vm_description, ... "
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vms": {
                    "admin-wazuh": {
                        "vm_id": 1000,
                        "vm_description": "Wazuh - dashboard",
                        "vm_ip": "192.168.42.100",
                    },
                    "admin-web-api-kong": {
                        "vm_id": 1020,
                        "vm_description": "API gateway",
                        "vm_ip": "192.168.42.120",
                    },
                }
            }
        }
    }


class BundleCreateAdminVmsItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleCreateAdminVmsReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleCreateAdminVmsItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Create Student VMs (Default)
# ---------------------------------------------------------------------------

class BundleCreateStudentVmsItemRequest(BaseModel):

    vm_id: int = Field(
        ...,
        ge=1,
        description="Virtual machine id",
    )

    vm_ip: str = Field(
        ...,
        description="vm ipv4",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    vm_description: str = Field(
        ...,
        strip_whitespace=True,
        max_length=200,
        # pattern=VM_DESCRIPTION_RE,
        description="Description"
    )

class BundleCreateStudentVmsRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vms: Dict[str, BundleCreateStudentVmsItemRequest] = Field(
        ...,
        description="Map <ssh_hostname> - vm override vm_id vm_ip vm_description, ... "
    )


    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vms": {
                    "student-box-01": {
                        "vm_id": 3001,
                        "vm_description": "student R42 student vm",
                        "vm_ip":  "192.168.42.160" ,
                    }
                }
            }
        }
    }


class BundleCreateStudentVmsItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleCreateStudentVmsReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleCreateStudentVmsItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Create Vuln VMs (Default)
# ---------------------------------------------------------------------------

class BundleCreateVulnVmsItemRequest(BaseModel):

    vm_id: int = Field(
        ...,
        ge=1,
        description="Virtual machine id",
    )

    vm_ip: str = Field(
        ...,
        description="vm ipv4",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    vm_description: str = Field(
        ...,
        strip_whitespace=True,
        max_length=200,
        # pattern=VM_DESCRIPTION_RE,
        description="Description"
    )

class BundleCreateVulnVmsRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vms: Dict[str, BundleCreateVulnVmsItemRequest] = Field(
        ...,
        description="Map <ssh_hostname> - vm override vm_id vm_ip vm_description, ... "
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vms": {
                    "vuln-box-00": {
                        "vm_id": 4000,
                        "vm_description": "vulnerable vm 00",
                        "vm_ip": "192.168.42.170",
                    },
                }
            }
        }
    }


class BundleCreateVulnVmsItemReply(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleCreateVulnVmsReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleCreateVulnVmsItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Revert Snapshot Default (Admin/Vuln/Student VMs)
# ---------------------------------------------------------------------------

class BundleRevertSnapshotDefaultRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to create",
        pattern=r"^[A-Za-z0-9_-]+$"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_snapshot_name": "default-snapshot-from-API-220925-1734",
                "as_json": True,

            }
        }
    }


# ---------------------------------------------------------------------------
# Start/Stop/Pause/Resume Default (Admin/Vuln/Student VMs)
# ---------------------------------------------------------------------------

class BundleStartStopDefaultRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,

            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# bundles/core/linux/ubuntu/configure/add_user.py
Request_BundlesCoreLinuxUbuntuConfigure_AddUser = BundleAddUserRequest
Reply_BundlesCoreLinuxUbuntuConfigure_AddUserItem = BundleAddUserItemReply
Reply_BundlesCoreLinuxUbuntuConfigure_AddUser = BundleAddUserReply

# bundles/core/linux/ubuntu/install/basic_packages.py
Request_BundlesCoreLinuxUbuntuInstall_BasicPackages = BundleBasicPackagesRequest
Reply_BundlesCoreLinuxUbuntuInstall_BasicPackagesItem = BundleBasicPackagesItemReply
Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages = BundleBasicPackagesReply

# bundles/core/linux/ubuntu/install/docker.py
Request_BundlesCoreLinuxUbuntuInstall_Docker = BundleDockerRequest
Reply_BundlesCoreLinuxUbuntuInstall_DockerItem = BundleDockerItemReply
Reply_BundlesCoreLinuxUbuntuInstall_Docker = BundleDockerReply

# bundles/core/linux/ubuntu/install/docker_compose.py
Request_BundlesCoreLinuxUbuntuInstall_DockerCompose = BundleDockerComposeRequest
Reply_BundlesCoreLinuxUbuntuInstall_DockerComposeItem = BundleDockerComposeItemReply
Reply_BundlesCoreLinuxUbuntuInstall_DockerCompose = BundleDockerComposeReply

# bundles/core/linux/ubuntu/install/dot_files.py
Request_BundlesCoreLinuxUbuntuInstall_DotFiles = BundleDotFilesRequest
Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem = BundleDotFilesItemReply
# NOTE: original file had a name collision; both Item and Reply were named
# Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem. We alias the Reply class too.

# bundles/core/proxmox/configure/default/vms/create_vms_admin_default.py
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVmsItem = BundleCreateAdminVmsItemRequest
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms = BundleCreateAdminVmsRequest
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVmsItem = BundleCreateAdminVmsItemReply
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateAdminVms = BundleCreateAdminVmsReply

# bundles/core/proxmox/configure/default/vms/create_vms_student_default.py
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem = BundleCreateStudentVmsItemRequest
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms = BundleCreateStudentVmsRequest
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem = BundleCreateStudentVmsItemReply
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms = BundleCreateStudentVmsReply

# bundles/core/proxmox/configure/default/vms/create_vms_vuln_default.py
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem = BundleCreateVulnVmsItemRequest
Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms = BundleCreateVulnVmsRequest
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem = BundleCreateVulnVmsItemReply
Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms = BundleCreateVulnVmsReply

# bundles/core/proxmox/configure/default/vms/revert_snapshot_default.py
Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms = BundleRevertSnapshotDefaultRequest

# bundles/core/proxmox/configure/default/vms/start_stop_resume_pause_default.py
Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms = BundleStartStopDefaultRequest
