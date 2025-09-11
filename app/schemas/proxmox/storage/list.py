

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #17
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxStorage_List(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    storage_name: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox storage name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxStorage_ListItem(BaseModel):

    action: Literal["vm_list_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    #
    storage_active: int
    storage_content_types: str
    storage_is_enable: int
    storage_is_share: int
    storage_name : str
    storage_space_available: int
    storage_space_total: int
    storage_space_used: int
    storage_space_used_fraction: float
    storage_type: str

class Reply_ProxmoxStorage_List(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxStorage_ListItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                    "action": "storage_list",
                    "proxmox_node": "px-testing",
                    "source": "proxmox",
                    #
                    "storage_active": 1,
                    "storage_content_types": "images,rootdir",
                    "storage_is_enable": 1,
                    "storage_is_share": 0,
                    "storage_name": "local-lvm",
                    "storage_space_available": 3600935440666,
                    "storage_space_total": 3836496314368,
                    "storage_space_used": 235560873702,
                    "storage_space_used_fraction": 0.0613999999999491,
                    "storage_type": "lvmthin"
                    }
                ]
            }
        }
    }
