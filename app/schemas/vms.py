"""Consolidated VM schemas: list, create, delete, clone, start/stop, mass ops."""

from enum import Enum
from typing import List, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# VM List
# ---------------------------------------------------------------------------


class VmListRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {"proxmox_node": "px-testing", "as_json": True}
        }
    }


class VmListActionEnum(str, Enum):
    LIST = "vm_list"
    START = "vm_start"
    STOP = "vm_stop"
    RESUME = "vm_resume"
    PAUSE = "vm_pause"
    STOP_FORCE = "vm_stop_force"


class VmListStatusEnum(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"


class VmListMetaReply(BaseModel):
    cpu_current_usage: int
    cpu_allocated: int
    disk_current_usage: int
    disk_read: int
    disk_write: int
    disk_max: int
    ram_current_usage: int
    ram_max: int
    net_in: int
    net_out: int


class VmListInfoReply(BaseModel):
    action: VmListActionEnum
    source: str = Field("proxmox", description="data source provider")
    proxmox_node: str
    vm_name: str
    vm_status: VmListStatusEnum
    vm_id: int
    vm_uptime: int
    vm_meta: VmListMetaReply


class VmListReply(BaseModel):
    rc: int = Field(..., description="RETURN CODE (0 = OK) ")
    result: List[List[VmListInfoReply]]


# ---------------------------------------------------------------------------
# VM List Usage
# ---------------------------------------------------------------------------


class VmListUsageRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {"proxmox_node": "px-testing", "as_json": True}
        }
    }


class VmListUsageItemReply(BaseModel):
    action: Literal["vm_list_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")
    cpu_allocated: int
    cpu_current_usage: int
    disk_current_usage: int
    disk_max: int
    disk_read: int
    disk_write: int
    net_in: int
    net_out: int
    ram_current_usage: int
    ram_max: int
    vm_status: str
    vm_uptime: int


class VmListUsageReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmListUsageItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "cpu_allocated": 1,
                        "cpu_current_usage": 0,
                        "disk_current_usage": 0,
                        "disk_max": 34359738368,
                        "disk_read": 0,
                        "disk_write": 0,
                        "net_in": 280531583,
                        "net_out": 6330590,
                        "ram_current_usage": 1910544625,
                        "ram_max": 4294967296,
                        "vm_id": 1020,
                        "vm_name": "admin-web-api-kong",
                        "vm_status": "running",
                        "vm_uptime": 79940,
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Create
# ---------------------------------------------------------------------------


class VmCreateRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_name: str = Field(
        ...,
        # default="new-vm",
        description="Virtual machine meta name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    vm_cpu: str = Field(
        ...,
        # default= "host",
        description="CPU type/model - host)",
        pattern=r"^[A-Za-z0-9._-]+$",
    )

    vm_cores: int = Field(
        ...,
        # default=1,
        ge=1,
        description="Number of cores per socket",
    )

    vm_sockets: int = Field(
        ...,
        # default=1,
        ge=1,
        description="Number of CPU sockets",
    )

    vm_memory: int = Field(
        ...,
        # default=1024,
        ge=128,
        description="Memory in MiB",
    )

    vm_disk_size: int | None = Field(
        default=None, ge=1, description="Disk size in GiB - optional"
    )

    vm_iso: str | None = Field(
        default=None,
        description="ISO volume path like 'local:iso/xxx.iso' - optional",
        pattern=r"^[A-Za-z0-9._-]+:iso/.+\.iso$",
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
                "vm_iso": "local:iso/ubuntu-24.04.2-live-server-amd64.iso",
            }
        }
    }


class VmCreateItemReply(BaseModel):
    action: Literal["vm_create"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_name: str
    vm_cpu: str
    vm_cores: int = Field(..., ge=1)
    vm_sockets: int = Field(..., ge=1)
    vm_memory: int = Field(..., ge=1)
    vm_net0: str
    vm_scsi0: str
    raw_data: str = Field(..., description="Raw string returned by Proxmox")


class VmCreateReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmCreateItemReply]

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
                        "vm_sockets": 1,
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Delete
# ---------------------------------------------------------------------------


class VmDeleteRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
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


class VmDeleteItemReply(BaseModel):
    action: Literal["vm_delete"]
    source: Literal["proxmox"]
    proxmox_node: str

    vm_id: int = Field(..., ge=1)
    vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class VmDeleteReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_delete",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        "vm_id": 1023,
                        "vm_name": "admin-web-deployer-ui",
                        "raw_data": "UPID:px-testing:123123:1123D4:68BFF2C7:qmdestroy:1023:API_master@pam!API_master:",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Clone
# ---------------------------------------------------------------------------


class VmCloneRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_new_id: str = Field(
        ...,
        # default="5005",
        description="New virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_description: str | None = Field(
        default="cloned-vm",
        description="Virtual machine meta description field",
        pattern=r"^[A-Za-z0-9\s.,_\-]*$",
    )

    vm_name: str = Field(
        ...,
        # default="new-vm",
        description="Virtual machine meta name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "2000",
                "vm_new_id": "3000",
                "vm_name": "test-cloned",
                "vm_description": "my description",
            }
        }
    }


class VmCloneItemReply(BaseModel):
    action: Literal["vm_clone"]
    source: Literal["proxmox"]
    proxmox_node: str
    vm_id: int = Field(..., ge=1)
    vm_id_clone_from: int = Field(..., ge=1)
    vm_name: str
    vm_description: str
    raw_info: str = Field(..., description="Raw string returned by proxmox")


class VmCloneReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmCloneItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "vm_clone",
                        "proxmox_node": "px-testing",
                        "raw_info": {
                            "data": "UPID:px-testing:0027CE9B:167F1A2C:68C03C17:qmclone:4004:API_master@pam!API_master:"
                        },
                        "source": "proxmox",
                        "vm_description": "my description",
                        "vm_id": "5004",
                        "vm_id_clone_from": "4004",
                        "vm_name": "test-cloned",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# VM Action (Start / Stop / Resume / Pause)
# ---------------------------------------------------------------------------


class VmActionRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="1000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "2000",
            }
        }
    }


class VmActionItemReply(BaseModel):
    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force"]
    source: Literal["proxmox"]

    proxmox_node: str
    vm_id: str  # int = Field(..., ge=1)
    vm_name: str


class VmActionReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmActionItemReply]

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
                        },
                    },
                    {
                        "action": "vm_delete",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        "vm_id": 4002,
                        "vm_name": "vuln-box-02",
                        "raw_data": {
                            "data": "UPID:px-testing:003364A6:1D261A84:68D143C6:qmdestroy:4002:API_master@pam!API_master:"
                        },
                    },
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Mass Delete
# ---------------------------------------------------------------------------


class MassDeleteVmItem(BaseModel):
    id: str = Field(..., description="Virtual machine id", pattern=r"^[0-9]+$")

    name: str = Field(
        ...,
        description="Virtual machine meta name",
        pattern="^[A-Za-z0-9-]+$",  #  deny void name
        # pattern=r"^[A-Za-z0-9-]*$",
    )


class MassDeleteRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )

    vms: List[MassDeleteVmItem] = Field(
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
                    {"id": "4000", "name": "vuln-box-00"},
                    {"id": "4001", "name": "vuln-box-01"},
                    {"id": "4002", "name": "vuln-box-02"},
                ],
            }
        }
    }


class MassDeleteItemReply(BaseModel):
    action: Literal["vm_start", "vm_stop", "vm_resume", "vm_pause", "vm_stop_force"]
    source: Literal["proxmox"]

    proxmox_node: str
    vm_id: str  # int = Field(..., ge=1)
    # vm_new_id   : str  # int = Field(..., ge=1)
    vm_name: str
    vm_status: Literal["running", "stopped", "paused"]


class MassDeleteReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[MassDeleteItemReply]


# ---------------------------------------------------------------------------
# Mass Start / Stop / Resume / Pause
# ---------------------------------------------------------------------------


class MassActionRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_ids: List[str] = Field(
        ...,
        # default="1000",
        description="Virtual machine id",
        min_items=1,
        # pattern=r"^[0-9]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                "vm_ids": ["4000", "4001"],
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# vm_list.py
Request_ProxmoxVms_VmList = VmListRequest
Reply_ProxmoxVms_VmList = VmListActionEnum
Reply_ProxmoxVmList_VmStatus = VmListStatusEnum
Reply_ProxmoxVmList_VmMeta = VmListMetaReply
Reply_ProxmoxVmList_VmInfo = VmListInfoReply
Reply_ProxmoxVmList = VmListReply

# vm_list_usage.py
Request_ProxmoxVms_VmListUsage = VmListUsageRequest
Reply_ProxmoxVms_VmListUsageItem = VmListUsageItemReply
Reply_ProxmoxVms_VmListUsage = VmListUsageReply

# vm_id/create.py
Request_ProxmoxVmsVMID_Create = VmCreateRequest
Reply_ProxmoxVmsVMID_CreateItem = VmCreateItemReply
Reply_ProxmoxVmsVMID_Create = VmCreateReply

# vm_id/delete.py
Request_ProxmoxVmsVMID_Delete = VmDeleteRequest
Reply_ProxmoxVmsVMID_DeleteItem = VmDeleteItemReply
Reply_ProxmoxVmsVMID_Delete = VmDeleteReply

# vm_id/clone.py
Request_ProxmoxVmsVMID_Clone = VmCloneRequest
Reply_ProxmoxVmsVMID_CloneItem = VmCloneItemReply
Reply_ProxmoxVmsVMID_Clone = VmCloneReply

# vm_id/start_stop_resume_pause.py
Request_ProxmoxVmsVMID_StartStopPauseResume = VmActionRequest
Reply_ProxmoxVmsVMID_StartStopPauseResumeItem = VmActionItemReply
Reply_ProxmoxVmsVMID_StartStopPauseResume = VmActionReply

# vm_ids/mass_delete.py
vm = MassDeleteVmItem
Request_ProxmoxVmsVmIds_MassDelete = MassDeleteRequest
Reply_ProxmoxVmsVMID_MasseDeleteItem = MassDeleteItemReply
Reply_ProxmoxVmsVmIds_MassDelete = MassDeleteReply

# vm_ids/mass_start_stop_resume_pause.py
Request_ProxmoxVmsVmIds_MassStartStopPauseResume = MassActionRequest
