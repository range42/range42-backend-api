

from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #7
#

class Request_ProxmoxVmsVMID_VmSetTag(BaseModel):

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

    # vm_tag_name: list[str] = Field(
    #     ...,
    #     description="list of tags to assign to the virtual machine",
    #     pattern = r"^[A-Za-z0-9_, -]+$"
    # )

    vm_tag_name: str = Field(
        ...,
        description="Comma separated list of tags to assign to the virtual machine",
        pattern=r"^[A-Za-z0-9_, -]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_tag_name":"group_01,group_02",
                "as_json": True,
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_VmSetTagItem(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_VmSetTag(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_VmSetTagItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_set_tag",
                        "source": "proxmox",
                        "tags": "group_01,group_02",
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
