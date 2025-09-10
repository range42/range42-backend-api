

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #5
#



#### #### #### #### #### #### #### #### #### #### #### #### #### ####
class Request_ProxmoxVms_VmListUsage(BaseModel):

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

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True
            }
        }
    }
#### #### #### #### #### #### #### #### #### #### #### #### #### ####


class Reply_ProxmoxVms_VmListUsageItem(BaseModel):

    action: Literal["vm_list_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")
    cpu_allocated: int
    cpu_current_usage: int
    disk_current_usage: int
    disk_max: int
    disk_read: int
    disk_write: int
    net_in: int
    net_out: int
    ram_current_usage: int
    ram_max: int
    vm_status: str
    vm_uptime: int


class Reply_ProxmoxVms_VmListUsage(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVms_VmListUsageItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "cpu_allocated":1,
                        "cpu_current_usage":0,
                        "disk_current_usage":0,
                        "disk_max":34359738368,
                        "disk_read":0,
                        "disk_write":0,
                        "net_in":280531583,
                        "net_out":6330590,
                        "ram_current_usage":1910544625,
                        "ram_max":4294967296,
                        "vm_id":1020,
                        "vm_name":"admin-web-api-kong",
                        "vm_status":"running",
                        "vm_uptime":79940
                    }
                ]
            }
        }
    }
