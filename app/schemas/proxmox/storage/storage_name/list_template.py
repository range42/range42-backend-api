

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #17
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxStorage_ListTemplate(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    storage_name: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox storage name",
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

class Reply_ProxmoxStorageWithStorageName_ListTemplateItem(BaseModel):

    action: Literal["storage_list_template"]
    source: Literal["proxmox"]
    proxmox_node: str

    # vm_id: int = Field(..., ge=1)
    storage_name: str
    template_content: str
    template_ctime: int
    template_format: str
    template_size: int
    template_vol_id: str

class Reply_ProxmoxStorageWithStorageName_ListTemplate(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxStorageWithStorageName_ListTemplateItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                {
                    "action": "storage_list_template",
                    "proxmox_node": "px-testing",
                    "source": "proxmox",
                    #
                    "storage_name": "local",
                    "template_content": "vztmpl",
                    "template_ctime": 1749734175,
                    "template_format": "tzst",
                    "template_size": 126515062,
                    "template_vol_id": "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
                }
                ]
            }
        }
    }
