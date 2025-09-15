

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
class Request_ProxmoxFirewall_DistableFirewallNode(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_DistableFirewallNodeItem(BaseModel):

    action: Literal["vm_EnableFirewallNode_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    node_firewall: str

class Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_DistableFirewallNodeItem]

    #
    # missing feat in role ?
    #
    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_node_enable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "node_firewall": "disabled",
                    }
                ]
            }
        }
    }
