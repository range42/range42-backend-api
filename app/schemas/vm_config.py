"""Consolidated VM config schemas: get config, get cdrom, get cpu, get ram, set tag."""

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# VM Get Config
# ---------------------------------------------------------------------------

class VmGetConfigRequest(BaseModel):

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
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
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


class VmGetConfigItemReply(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class VmGetConfigReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmGetConfigItemReply]

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


# ---------------------------------------------------------------------------
# VM Get Config CDROM
# ---------------------------------------------------------------------------

class VmGetConfigCdromRequest(BaseModel):

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
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
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


class VmGetConfigCdromItemReply(BaseModel):

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


class VmGetConfigCdromReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmGetConfigCdromItemReply]

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


# ---------------------------------------------------------------------------
# VM Get Config CPU
# ---------------------------------------------------------------------------

class VmGetConfigCpuRequest(BaseModel):

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
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
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


class VmGetConfigCpuItemReply(BaseModel):

    action: Literal["vm_get_config_cpu"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id       : str # int = Field(..., ge=1)
    vm_arch     : str #to fix ?
    vm_cores    : str #to fix ?
    vm_sockets  : str #to fix ?
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class VmGetConfigCpuReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmGetConfigCpuItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action":"vm_get_config_cpu",
                        "proxmox_node":"px-testing",
                        "source":"proxmox",
                        "vm_arch":"host",
                        "vm_cores":"2",
                        "vm_id":"1000",
                        "vm_sockets":"1"
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Get Config RAM
# ---------------------------------------------------------------------------

class VmGetConfigRamRequest(BaseModel):

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
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
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


class VmGetConfigRamItemReply(BaseModel):

    action: Literal["vm_get_config_ram"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id           : str # int = Field(..., ge=1)
    vm_ram_allocated: str # wtf... - fix todo

    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class VmGetConfigRamReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmGetConfigRamItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_get_config_ram",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        "vm_id": "1000",
                        "vm_ram_allocated": "8192"
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Set Tag
# ---------------------------------------------------------------------------

class VmSetTagRequest(BaseModel):

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
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    vm_tag_name: str = Field(
        ...,
        description="Comma separated list of tags to assign to the virtual machine",
        pattern=r"^[A-Za-z0-9_, -]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_tag_name":"group_01,group_02",
                "as_json": True,
            }
        }
    }


class VmSetTagItemReply(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class VmSetTagReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmSetTagItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_set_tag",
                        "source": "proxmox",
                        "tags": "group_01,group_02",
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# vm_id/config/vm_get_config.py
Request_ProxmoxVmsVMID_VmGetConfig = VmGetConfigRequest
Reply_ProxmoxVmsVMID_VmGetConfigItem = VmGetConfigItemReply
Reply_ProxmoxVmsVMID_VmGetConfig = VmGetConfigReply

# vm_id/config/vm_get_config_cdrom.py
Request_ProxmoxVmsVMID_VmGetConfigCdrom = VmGetConfigCdromRequest
Reply_ProxmoxVmsVMID_VmGetConfigCdromItem = VmGetConfigCdromItemReply
Reply_ProxmoxVmsVMID_VmGetConfigCdrom = VmGetConfigCdromReply

# vm_id/config/vm_get_config_cpu.py
Request_ProxmoxVmsVMID_VmGetConfigCpu = VmGetConfigCpuRequest
Reply_ProxmoxVmsVMID_VmGetConfigCpuItem = VmGetConfigCpuItemReply
Reply_ProxmoxVmsVMID_VmGetConfigCpu = VmGetConfigCpuReply

# vm_id/config/vm_get_config_ram.py
Request_ProxmoxVmsVMID_VmGetConfigRam = VmGetConfigRamRequest
Reply_ProxmoxVmsVMID_VmGetConfigRamItem = VmGetConfigRamItemReply
Reply_ProxmoxVmsVMID_VmGetConfigRam = VmGetConfigRamReply

# vm_id/config/vm_set_tag.py
Request_ProxmoxVmsVMID_VmSetTag = VmSetTagRequest
Reply_ProxmoxVmsVMID_VmSetTagItem = VmSetTagItemReply
Reply_ProxmoxVmsVMID_VmSetTag = VmSetTagReply
