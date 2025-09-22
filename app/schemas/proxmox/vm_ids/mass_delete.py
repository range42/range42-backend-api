
from typing import Literal, List
from pydantic import BaseModel, Field



class vm(BaseModel):

    id: str = Field(
        ...,
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    name: str = Field(
        ...,
        description="Virtual machine meta name",
        pattern="^[A-Za-z0-9-]+$" #  deny void name
        # pattern=r"^[A-Za-z0-9-]*$",
    )


class Request_ProxmoxVmsVmIds_MassDelete(BaseModel):

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

    vms: List[vm] = Field(
        ...,
        description="List of virtual machine (vm_id + vm_name)",
        min_length=1,
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                "vms": [
                    {"id":"4000", "name":"vuln-box-00"},
                    {"id":"4001", "name":"vuln-box-01"},
                    {"id":"4002", "name":"vuln-box-02"},
                ],
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_MasseDeleteItem(BaseModel):

    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force" ]
    source: Literal["proxmox"]

    proxmox_node: str
    vm_id       : str  # int = Field(..., ge=1)
    # vm_new_id   : str  # int = Field(..., ge=1)
    vm_name     : str
    vm_status: Literal["running", "stopped", "paused"]

class Reply_ProxmoxVmsVmIds_MassDelete(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_MasseDeleteItem]


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
