

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_EnableFirewallVm(BaseModel):

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

    # vm_name: str = Field( # todo - to_fix : add resolver vm_name - vm_id
    #     ...,
    #     # default= "px-testing",
    #     description = "Proxmox storage name",
    #     pattern=r"^[A-Za-z0-9-]*$"
    # )

    # vm_firewall: str = Field(
    #     ...,
    #     # default= "px-testing",
    #     description = "Proxmox storage name",
    #     pattern=r"^[A-Za-z0-9-]*$"
    # )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_name": "test",
                "vm_id": "1000",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_EnableFirewallVmItem(BaseModel):

    action: Literal["vm_EnableFirewallVm_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_name: str
    vm_firewall: str

class Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_EnableFirewallVmItem]


    #
    # output missing - missing feat in role ?
    #
    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_enable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "100",
                        "vm_firewall": "enabled",
                        "vm_name": "test"
                    }
                ]
            }
        }
    }
