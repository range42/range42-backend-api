
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #3
#


class Request_ProxmoxVmsVMID_Clone(BaseModel):

    # '{"vm_id":100,
    # "vm_new_id":1001,
    # "vm_name":"test-cloned",
    # "vm_description":"my description"}'

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

    vm_new_id: str = Field(
        ...,
        # default="5005",
        description="New virtual machine id",
        pattern=r"^[0-9]+$"
    )

    vm_description: str | None = Field(
        default="cloned-vm",
        description="Virtual machine meta description field",
        pattern = r"^[A-Za-z0-9\s.,_\-]*$"
    )

    vm_name: str  = Field(
        ...,
        # default="new-vm",
        description="Virtual machine meta name",
        pattern = r"^[A-Za-z0-9-]*$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "2000",
                "vm_new_id": "3000",
                "vm_name":"test-cloned",
                "vm_description":"my description"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_CloneItem(BaseModel):

    action: Literal["vm_clone"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_id_clone_from: int = Field(..., ge=1)
    vm_name: str
    vm_description: str
    raw_info: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_Clone(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_CloneItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_clone",
                        "proxmox_node": "px-testing",
                        "raw_info": {
                            "data": "UPID:px-testing:0027CE9B:167F1A2C:68C03C17:qmclone:4004:API_master@pam!API_master:"},
                        "source": "proxmox",
                        "vm_description": "my description",
                        "vm_id": "5004",
                        "vm_id_clone_from": "4004",
                        "vm_name": "test-cloned"
                    }
               ]
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
