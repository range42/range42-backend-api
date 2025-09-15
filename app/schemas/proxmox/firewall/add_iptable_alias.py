

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_AddIptablesAlias(BaseModel):

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

    vm_fw_alias_name: str = Field(
        ...,
        description="Firewall alias name",
        pattern=r"^[A-Za-z0-9-_]+$",
    )

    vm_fw_alias_cidr: str = Field(
        ...,
        description="CIDR notation for the alias - eg 192.168.123.0/24",
        pattern=r"^[0-9./]+$",
    )

    vm_fw_alias_comment: str | None = Field(
        ...,
        description="Optional comment for the firewall alias",
        pattern=r"^[A-Za-z0-9 _-]*$",
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
                "vm_id":"1000",
                #
                "vm_fw_alias_name":"test",
                "vm_fw_alias_cidr":"192.168.123.0/24",
                "vm_fw_alias_comment":"this_comment"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_AddIptablesAliasItem(BaseModel):

    action: Literal["vm_ListIso_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_cidr: str
    vm_fw_alias_name: str
    vm_id: str

class Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_AddIptablesAliasItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_add_iptables_alias",
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
