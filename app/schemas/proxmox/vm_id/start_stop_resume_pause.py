
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #3
#


class Request_ProxmoxVmsVMID_StartStopPauseResume(BaseModel):

    vm_id: str | None = Field(
        default="1000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    proxmox_node: str | None = "px-testing"
    as_json: bool | None = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_StartStopPauseResumeItem(BaseModel):

    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force" ]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    vm_status: Literal["running", "stopped", "paused"]

class Reply_ProxmoxVmsVMID_StartStopPauseResume(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_StartStopPauseResumeItem]


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
