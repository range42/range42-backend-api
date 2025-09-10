
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #5
#

class Request_ProxmoxVmsVMID_VmGetConfigCdrom(BaseModel):

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

class Reply_ProxmoxVmsVMID_VmGetConfigCdromItem(BaseModel):

    action: Literal["vm_get_config_cdrom"]
    source: Literal["proxmox"]
    proxmox_node   : str
    vm_id          : str # int = Field(..., ge=1)
    vm_cdrom_device: str
    vm_cdrom_iso   : str
    vm_cdrom_media : str
    vm_cdrom_size  : str

    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_VmGetConfigCdrom(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_VmGetConfigCdromItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_get_config_cdrom",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        "vm_cdrom_device": "ide2",
                        "vm_cdrom_iso": "local:1000/vm-1000-cloudinit.qcow2",
                        "vm_cdrom_media": "cdrom",
                        "vm_cdrom_size": "4M",
                        "vm_id": "1000"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
