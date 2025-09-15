

from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #9
#

class Request_ProxmoxVmsVMID_CreateSnapshot(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to create",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    vm_snapshot_description: str | None = Field(
        default=None,
        description="Optional description for the snapshot",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_snapshot_name":"MY_VM_SNAPSHOT",
                "vm_snapshot_description":"MY_DESCRIPTION",
                "as_json": True
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_CreateSnapshotItem(BaseModel):

    action: Literal["vm_get_config"]
    proxmox_node: str
    source: Literal["proxmox"]
    vm_id: str # int = Field(..., ge=1)

    vm_name: str
    vm_snapshot_description: str
    vm_snapshot_name: str

    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_CreateSnapshot(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_CreateSnapshotItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "snapshot_vm_create",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_name": "admin-wazuh",
                        "vm_snapshot_description": "MY_DESCRIPTION",
                        "vm_snapshot_name": "MY_VM_SNAPSHOT",
                        "raw_data": { "data": "UPID:px-testing:002D5E30:1706941B:68C196E9:qmsnapshot:1000:API_master@pam!API_master:" }
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
