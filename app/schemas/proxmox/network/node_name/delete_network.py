

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #11
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxNetwork_WithNodeName_DeleteInterface(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    #

    iface_name: str | None = Field(
        description="Interface name",
        pattern=r"^[a-zA-Z0-9._-]+$"
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
                "as_json": True,
                #
                "iface_name":"vmbr42",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxNetwork_WithNodeName_DeleteInterfaceItem(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    iface_name: str
    # vm_id: int = Field(..., ge=1)
    # vm_id: str
    # vm_fw_pos: int

class Reply_ProxmoxNetwork_WithNodeName_DeleteInterface(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxNetwork_WithNodeName_DeleteInterfaceItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_delete_interfaces_node",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "iface_name": "vmbr42",
                    }
                ]
            }
        }
    }
