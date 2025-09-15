

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #11
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxNetwork_WithVmId_ListNetwork(BaseModel):

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

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                "vm_id": "1001",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxNetwork_WithVmId_ListNetworkInterfaceItem(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str

class  Reply_ProxmoxNetwork_WithVmId_ListNetworkInterface(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxNetwork_WithVmId_ListNetworkInterfaceItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_list_interfaces_vm",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "vm_network_bridge": "vmbr0",
                        "vm_network_device": "net0",
                        "vm_network_mac": "AA:BB:CC:DD:EE:FF",
                        "vm_network_type": "virtio"

                    }
                ]
            }
        }
    }
