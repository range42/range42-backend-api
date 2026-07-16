"""Consolidated bundle schemas: Ubuntu packages/configure.
This __init__.py serves double duty:
1. Makes bundles/ a proper Python package so old imports (app.schemas.bundles.core...) keep working.
2. Exposes consolidated schema classes for new code to import from app.schemas.bundles.
"""

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
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )
    hosts: str = Field(
        ..., description="Hosts or groups", pattern=r"^[a-zA-Z0-9._:-]+$"
    )
    ####
    user: str = Field(
        ...,
        description="New user",
        pattern=r"^[a-z_][a-z0-9_-]*$",
    )
    password: str = Field(
        ...,
        description="New password",
        pattern=r"^[A-Za-z0-9@._-]*$",  # dangerous chars removed.
    )
    change_pwd_at_logon: bool = Field(
        ..., description="Force user to change password on first login"
    )
    shell_path: str = Field(
        ..., description="Default user shell ", pattern=r"^/[a-z/]*$"
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
    proxmox_node: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleAddUserReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleAddUserItemReply]

    model_config = {"json_schema_extra": {"example": {"rc": 0, "result": [{}]}}}


# ---------------------------------------------------------------------------
# Install Basic Packages
# ---------------------------------------------------------------------------


class BundleBasicPackagesRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )
    hosts: str = Field(
        ..., description="Hosts or groups", pattern=r"^[a-zA-Z0-9._:-]+$"
    )
    ####
    install_package_basics: bool = Field(..., description="")
    install_package_firewalls: bool = Field(..., description="")
    install_package_docker: bool = Field(..., description="")
    install_package_docker_compose: bool = Field(..., description="")
    install_package_utils_json: bool = Field(..., description="")
    install_package_utils_network: bool = Field(..., description="")
    ####
    install_ntpclient_and_update_time: bool = Field(..., description="")
    packages_cleaning: bool = Field(..., description="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_basics": True,
                "install_package_firewalls": False,
                "install_package_docker": False,
                "install_package_docker_compose": False,
                "install_package_utils_json": False,
                "install_package_utils_network": False,
                "install_ntpclient_and_update_time": True,
                "packages_cleaning": True,
            }
        }
    }


class BundleBasicPackagesItemReply(BaseModel):
    proxmox_node: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleBasicPackagesReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleBasicPackagesItemReply]

    model_config = {"json_schema_extra": {"example": {"rc": 0, "result": [{}]}}}


# ---------------------------------------------------------------------------
# Install Docker
# ---------------------------------------------------------------------------


class BundleDockerRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )
    hosts: str = Field(
        ..., description="Hosts or groups", pattern=r"^[a-zA-Z0-9._:-]+$"
    )
    ####
    install_package_docker: bool = Field(..., description="")
    ####
    install_ntpclient_and_update_time: bool = Field(..., description="")
    packages_cleaning: bool = Field(..., description="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_docker": True,
                "install_ntpclient_and_update_time": True,
                "packages_cleaning": True,
            }
        }
    }


class BundleDockerItemReply(BaseModel):
    proxmox_node: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleDockerReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDockerItemReply]

    model_config = {"json_schema_extra": {"example": {"rc": 0, "result": [{}]}}}


# ---------------------------------------------------------------------------
# Install Docker Compose
# ---------------------------------------------------------------------------


class BundleDockerComposeRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )
    hosts: str = Field(
        ..., description="Hosts or groups", pattern=r"^[a-zA-Z0-9._:-]+$"
    )
    ####
    install_package_docker: bool = Field(..., description="")
    install_package_docker_compose: bool = Field(..., description="")
    ####
    install_ntpclient_and_update_time: bool = Field(..., description="")
    packages_cleaning: bool = Field(..., description="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                #
                "install_package_docker": True,
                "install_package_docker_compose": True,
                "install_ntpclient_and_update_time": True,
                "packages_cleaning": True,
            }
        }
    }


class BundleDockerComposeItemReply(BaseModel):
    proxmox_node: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleDockerComposeReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDockerComposeItemReply]

    model_config = {"json_schema_extra": {"example": {"rc": 0, "result": [{}]}}}


# ---------------------------------------------------------------------------
# Install Dot Files
# ---------------------------------------------------------------------------


class BundleDotFilesRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )
    hosts: str = Field(
        ..., description="Hosts or groups", pattern=r"^[a-zA-Z0-9._:-]+$"
    )
    ####
    user: str = Field(..., description="targeted username")
    install_vim_dot_files: bool = Field(
        ..., description="Install vim dot file in user directory"
    )
    install_zsh_dot_files: bool = Field(
        ..., description="Install zsh dot file in user directory"
    )
    apply_for_root: bool = Field(..., description="Install dot files in /root")

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
    proxmox_node: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class BundleDotFilesReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[BundleDotFilesItemReply]

    model_config = {"json_schema_extra": {"example": {"rc": 0, "result": [{}]}}}


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
