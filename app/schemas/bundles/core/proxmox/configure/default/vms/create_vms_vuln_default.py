
#
# ISSUE - #30
#

from typing import Dict, Optional
from pydantic import BaseModel, Field


class Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem(BaseModel):

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

class Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vms: Dict[str, Request_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem] = Field(
        ...,
        description="Map <ssh_hostname> - vm override vm_id vm_ip vm_description, ... "
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vms": {
                    "vuln-box-00": {
                        "vm_id": 4000,
                        "vm_description": "vulnerable vm 00",
                        "vm_ip": "192.168.42.170",
                    },
                    "vuln-box-01": {
                        "vm_id": 4001,
                        "vm_description": "vulnerable vm 01",
                        "vm_ip": "192.168.42.171",
                    },
                    "vuln-box-02": {
                        "vm_id": 4002,
                        "vm_description": "vulnerable vm 02",
                        "vm_ip": "192.168.42.172",
                    },
                    "vuln-box-03": {
                        "vm_id": 4003,
                        "vm_description": "vulnerable vm 03",
                        "vm_ip": "192.168.42.173",
                    },
                    "vuln-box-04": {
                        "vm_id": 4004,
                        "vm_description": "vulnerable vm 04",
                        "vm_ip": "192.168.42.174",
                    },
                }
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVms(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_BundlesCoreProxmoxConfigureDefaultVms_CreateVulnVmsItem]

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
