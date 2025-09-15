
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #3
#


class Request_ProxmoxVmsVMID_StartStopPauseResume(BaseModel):

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
        # default="1000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )


    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "2000",
                # "vm_new_id": "3000",
                # "vm_name":"test-cloned",
                # "vm_description":"my description"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_StartStopPauseResumeItem(BaseModel):

    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force" ]
    source: Literal["proxmox"]

    proxmox_node: str
    vm_id       : str  # int = Field(..., ge=1)
    vm_new_id   : str  # int = Field(..., ge=1)
    vm_name     : str
    vm_status: Literal["running", "stopped", "paused"]

class Reply_ProxmoxVmsVMID_StartStopPauseResume(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_StartStopPauseResumeItem]


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
