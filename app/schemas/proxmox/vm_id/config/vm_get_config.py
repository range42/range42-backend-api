
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #5
#

class Request_ProxmoxVmsVMID_VmGetConfig(BaseModel):

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

class Reply_ProxmoxVmsVMID_VmGetConfigItem(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_ProxmoxVmsVMID_VmGetConfig(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxVmsVMID_VmGetConfigItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_get_config",
                        "proxmox_node": "px-testing",
                        "raw_data": {
                            "data": {
                                "balloon": 0,
                                "boot": "c",
                                "bootdisk": "scsi0",
                                "cipassword": "**********",
                                "ciuser": "alice",
                                "cores": 2,
                                "cpu": "host",
                                "digest": "29bec92_redacted",
                                "ide2": "local:1000/vm-1000-cloudinit.qcow2,media=cdrom,size=4M",
                                "ipconfig0": "ip=192.168.42.100/24,gw=192.168.42.1",
                                "memory": "8192",
                                "meta": "creation-qemu=9.0.2,ctime=1757418890",
                                "name": "admin-wazuh",
                                "net0": "virtio=BC:24:11:CB:B3:C7,bridge=vmbr0",
                                "scsi0": "local-lvm:vm-1000-disk-0,size=64G",
                                "scsihw": "virtio-scsi-pci",
                                "serial0": "socket",
                                "smbios1": "uuid=82c50ddc-a24f-4cbc-a013-c0e846f230fc",
                                "sockets": 1,
                                "sshkeys": "ssh-ed25519%20AAAAC....redacted",
                                "tags": "admin",
                                "vga": "serial0",
                                "vmgenid": "c7426562-ad4b-4719-81a1-72328f7ec018"
                            }
                        },
                        "source": "proxmox",
                        "vm_id": "1000"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
