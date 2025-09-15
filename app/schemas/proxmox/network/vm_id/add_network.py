

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #11
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxNetwork_WithVmId_AddNetwork(BaseModel):

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

    iface_model: str | None = Field(
        description="Interface model-  virtio, e1000, rtl8139",
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    # net_index: int | None = Field(
    #     description="Bridge name for interface - vmbr0, vmbr142",
    #     # pattern=r"^[A-Za-z0-9._-]+$"
    # )

    iface_bridge: str | None = Field(
        description="Bridge name for interface - vmbr0, vmbr142",
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    vm_vmnet_id: int | None = Field(
        description="Network device index - 0, 1, 2, ..."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
                #
                "vm_id": "1000",
                "vm_vmnet_id": "1",
                "iface_model": "virtio",
                "iface_bridge": "vmbr142",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxNetwork_WithVmId_AddNetworkInterfaceItem(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int

class Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxNetwork_WithVmId_AddNetworkInterfaceItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_add_interfaces_vm",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "iface_model": "virtio",
                    }
                ]
            }
        }
    }
