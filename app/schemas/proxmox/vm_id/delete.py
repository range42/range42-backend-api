
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #3
#


class Request_ProxmoxVmsVMID_Delete(BaseModel):

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
                "vm_id": "1111",
                "as_json": True,
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_DeleteItem(BaseModel):

    action: Literal["vm_delete"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_Delete(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_DeleteItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_delete",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        "vm_id": 1023,
                        "vm_name": "admin-web-deployer-ui",
                        "raw_data": "UPID:px-testing:123123:1123D4:68BFF2C7:qmdestroy:1023:API_master@pam!API_master:"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
