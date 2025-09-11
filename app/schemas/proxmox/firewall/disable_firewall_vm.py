

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
class Request_ProxmoxFirewall_DistableFirewallVm(BaseModel):

    proxmox_api_host: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox api - ip:port",
        pattern=r"^[A-Za-z0-9\.:-]*$"
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

                "proxmox_api_host": "127.0.0.1:18007",

            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_DistableFirewallVmItem(BaseModel):

    action: Literal["vm_EnableFirewallDc_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    proxmox_api_host: str

class Reply_ProxmoxFirewallWithStorageName_DistableFirewallVm(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_DistableFirewallVmItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_disable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "100",
                        "vm_firewall": "disable",
                        "vm_name": "test"
                    }
                ]
            }
        }
    }
