

from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #9
#

class Request_ProxmoxVmsVMID_ListSnapshot(BaseModel):

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
                "vm_id": "1111"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_ListSnapshotItem(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_ListSnapshot(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_ListSnapshotItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,

                "result": [
                    {
                        "action": "snapshot_vm_list",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_snapshot_description": "MY_DESCRIPTION",
                        "vm_snapshot_name": "MY_VM_SNAPSHOT",
                        "vm_snapshot_parent": "",
                        "vm_snapshot_time": 1757517545
                    },
                    {
                        "action": "snapshot_vm_list",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_snapshot_description": "You are here!",
                        "vm_snapshot_name": "current",
                        "vm_snapshot_parent": "MY_VM_SNAPSHOT",
                        "vm_snapshot_sha1": "7cc59c988bb8f18601fe076ad239f8b760667270"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
