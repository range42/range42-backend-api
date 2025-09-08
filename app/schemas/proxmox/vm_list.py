from enum import Enum
from typing import  List
from pydantic import BaseModel, Field

#
# ISSUE - #2
#


class Request_ProxmoxVms_VmList(BaseModel):

    proxmox_node: str | None = Field(
        default="px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]+$"
    )

    as_json: bool | None = True

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVms_VmList(str, Enum):

    LIST = "vm_list"
    START = "vm_start"
    STOP = "vm_stop"
    RESUME = "vm_resume"
    PAUSE = "vm_pause"
    STOP_FORCE = "vm_stop_force"


class Reply_ProxmoxVmList_VmStatus(str, Enum):

    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"


class Reply_ProxmoxVmList_VmMeta(BaseModel):

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


class Reply_ProxmoxVmList_VmInfo(BaseModel):

    action:  Reply_ProxmoxVms_VmList
    source: str = Field("proxmox", description="data source provider")
    proxmox_node: str
    vm_name: str
    vm_status:  Reply_ProxmoxVmList_VmStatus
    vm_id: int
    vm_uptime: int
    vm_meta:  Reply_ProxmoxVmList_VmMeta


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxVmList(BaseModel):

    rc: int = Field(..., description="RETURN CODE (0 = OK) ")
    result: List[List[ Reply_ProxmoxVmList_VmInfo]]

