
#
# ISSUE - #30
#

from typing import Dict, Optional
from pydantic import BaseModel, Field


class Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem(BaseModel):

    vm_id: int = Field(
        ...,
        ge=1,
        description="Virtual machine id",
    )

    # vm_id: str = Field(
    #     ...,
    #     # default="4000",
    #     description="Virtual machine id",
    #     pattern=r"^[0-9]+$"
    # )

    vm_ip: str = Field(
        ...,
        description="vm ipv4",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    vm_description: str = Field(
        ...,
        strip_whitespace=True,
        max_length=200,
        # pattern=VM_DESCRIPTION_RE,
        description="Description"
    )

class Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vms: Dict[str, Request_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem] = Field(
        ...,
        description="Map <ssh_hostname> - vm override vm_id vm_ip vm_description, ... "
    )


    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vms": {
                    "student-box-01": {
                        "vm_id": 3001,
                        "vm_description": "student R42 student vm",
                        "vm_ip":  "192.168.42.160" ,
                    }
                }
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVms(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateStudentVmsItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        # "action": "vm_get_config",
                        # "proxmox_node": "px-testing",
                        # "raw_data": {
                        #     "data": {
                        #       ...
                        #     }
                        # },
                        # "source": "proxmox",
                        # "vm_id": "1000"
                    }
                ]
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
