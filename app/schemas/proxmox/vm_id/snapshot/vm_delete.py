

from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #9
#

class Request_ProxmoxVmsVMID_DeleteSnapshot(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to delete",
        pattern=r"^[A-Za-z0-9_-]+$"
    )


    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_snapshot_name": "MY_VM_SNAPSHOT",
                "as_json": True
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_DeleteSnapshotItem(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_DeleteSnapshot(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_DeleteSnapshotItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                 "result": [
                    {
                        "action": "snapshot_vm_delete",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_name": "admin-wazuh",
                        "vm_snapshot_name": "BBBB",
                        "raw_data": { "data": "UPID:px-testing:002D6878:17077370:68C19925:qmdelsnapshot:1000:API_master@pam!API_master:"},
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
