

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_DeleteIptablesAlias(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vm_fw_alias_name: str = Field(
        ...,
        description="Firewall alias name",
        pattern=r"^[A-Za-z0-9-_]+$",
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

class Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAliasItem(BaseModel):

    action: Literal["vm_DeleteIptablesAlias_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_name: str
    vm_id: int


class Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAliasItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_delete_iptables_alias",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_alias_name": "test",
                        "vm_id": "100",
                    }
                ]
            }
        }
    }
