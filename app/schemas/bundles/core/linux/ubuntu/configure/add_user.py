
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #19
#

class Request_BundlesCoreLinuxUbuntuConfigure_AddUser(BaseModel):

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
                # "as_json": True,
                #
                "user": "elliot",
                "password": "r0b0t_aLd3rs0n",
                "change_pwd_at_logon": False,
                "shell_path": "/bin/sh",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_BundlesCoreLinuxUbuntuConfigure_AddUserItem(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_BundlesCoreLinuxUbuntuConfigure_AddUser(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_BundlesCoreLinuxUbuntuConfigure_AddUserItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        # "action": "vm_get_config",
                        # "proxmox_node": "px-testing",
                        # "raw_data": {
                        #     "data": {
                        #       ...
                        #     }
                        # },
                        # "source": "proxmox",
                        # "vm_id": "1000"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
