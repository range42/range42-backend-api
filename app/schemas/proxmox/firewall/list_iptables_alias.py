

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_ListIptablesAlias(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_ListIptablesAliasItem(BaseModel):

    action: Literal["vm_ListIptablesAlias_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_cidr: str
    vm_fw_alias_name: int
    vm_id: str

class Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_ListIptablesAliasItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_list_iptables_alias",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_alias_cidr": "192.168.123.0/24",
                        "vm_fw_alias_name": "test",
                        "vm_id": "1000"
                    }
                ]
            }
        }
    }
