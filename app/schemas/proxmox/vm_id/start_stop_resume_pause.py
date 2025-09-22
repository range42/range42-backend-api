
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

class Reply_ProxmoxVmsVMID_StartStopPauseResumeItem(BaseModel):

    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force" ]
    source: Literal["proxmox"]

    proxmox_node: str
    vm_id       : str  # int = Field(..., ge=1)
    vm_name     : str




class Reply_ProxmoxVmsVMID_StartStopPauseResume(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_StartStopPauseResumeItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [

                    {
                        "action": "vm_delete",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        "vm_id": 4001,
                        "vm_name": "vuln-box-01",
                        "raw_data": {
                            "data": "UPID:px-testing:0033649C:1D2619CC:68D143C5:qmdestroy:4001:API_master@pam!API_master:"
                        }
                    },
                    {
                        "action": "vm_delete",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        "vm_id": 4002,
                        "vm_name": "vuln-box-02",
                        "raw_data": {
                            "data": "UPID:px-testing:003364A6:1D261A84:68D143C6:qmdestroy:4002:API_master@pam!API_master:"
                        }
                    },
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
