
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #28
#

class Request_BundlesCoreLinuxUbuntuInstall_DotFiles(BaseModel):

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

    # as_json: bool = Field(
    #     default=True,
    #     description="If true : JSON output else : raw output"
    # )
    #

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
                # "as_json": True,
                #
                "user": "jane",
                "install_vim_dot_files": True,
                "install_zsh_dot_files": True,
                "apply_for_root": False,
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_BundlesCoreLinuxUbuntuInstall_DotFilesItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        # "action": "vm_get_config",
                        # "proxmox_node": "px-testing",
                        # ...
                        # "source": "proxmox",
                        # "vm_id": "1000"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
