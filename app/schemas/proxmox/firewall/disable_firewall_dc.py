

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
class Request_ProxmoxFirewall_DisableFirewallDc(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

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
                "proxmox_api_host": "127.0.0.1:1234",
                "as_json": True

            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_DisableFirewallDcItem(BaseModel):

    action: Literal["vm_DisableFirewallDc_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    # iso_content: str
    # iso_ctime: int
    # iso_format: str
    # iso_size: int
    # iso_vol_id: str
    # local: str
    # storage_name: str

class Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_DisableFirewallDcItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "storage_list_iso",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##

                    }
                ]
            }
        }
    }
