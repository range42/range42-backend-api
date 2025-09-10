
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #3
#

class Request_ProxmoxVmsVMID_Create(BaseModel):

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": 1111,
                "vm_name": "vm_with_local_iso",
                "vm_cpu": "host",
                "vm_cores": 2,
                "vm_sockets": 1,
                "vm_memory": 2042,
                "vm_disk_size": 42,
                "vm_iso": "local:iso/ubuntu-24.04.2-live-server-amd64.iso"
            }
        }
    }

class Request_ProxmoxVmsVMID_Create(BaseModel):

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

    vm_name: str  = Field(
        ...,
        # default="new-vm",
        description="Virtual machine meta name",
        pattern = r"^[A-Za-z0-9-]*$"
    )

    vm_cpu: str = Field(
        ...,
        # default= "host",
        description='CPU type/model - host)',
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    vm_cores: int = Field(
        ...,
        # default=1,
        ge=1,
        description="Number of cores per socket"
    )

    vm_sockets: int = Field(
        ...,
        # default=1,
        ge=1,
        description="Number of CPU sockets"
    )

    vm_memory: int = Field(
        ...,
        # default=1024,
        ge=128,
        description="Memory in MiB"
    )

    vm_disk_size: int | None = Field(
        default=None,
        ge=1,
        description="Disk size in GiB - optional"
    )

    vm_iso: str | None = Field(
        default=None,
        description="ISO volume path like 'local:iso/xxx.iso' - optional",
        pattern=r"^[A-Za-z0-9._-]+:iso/.+\.iso$"
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
                "vm_name": "new-vm",
                "vm_cpu": "host",
                "vm_cores": 2,
                "vm_sockets": 1,
                "vm_memory": 2042,
                "vm_disk_size": 42,
                "vm_iso": "local:iso/ubuntu-24.04.2-live-server-amd64.iso"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmsVMID_CreateItem(BaseModel):

    action: Literal["vm_create"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id     : int = Field(..., ge=1)
    vm_name   : str
    vm_cpu    : str
    vm_cores  : int = Field(..., ge=1)
    vm_sockets: int = Field(..., ge=1)
    vm_memory : int = Field(..., ge=1)
    vm_net0   : str
    vm_scsi0  : str
    raw_data  : str = Field(..., description="Raw string returned by Proxmox")


class Reply_ProxmoxVmsVMID_Create(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_CreateItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_create",
                        "proxmox_node": "px-testing",
                        "raw_data": "UPID:px-testing:00281144:16855865:68C04C13:qmcreate:9998:API_master@pam!API_master:",
                        "source": "proxmox",
                        "vm_cores": 2,
                        "vm_cpu": "host",
                        "vm_id": 9998,
                        "vm_memory": 2042,
                        "vm_name": "vm-with-local-iso-2",
                        "vm_net0": "virtio,bridge=vmbr0",
                        "vm_scsi0": "local-lvm:42,format=raw",
                        "vm_sockets": 1
                    }
                ]
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
