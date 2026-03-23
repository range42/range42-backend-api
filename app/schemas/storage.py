"""Consolidated storage schemas: list, download ISO, list ISO, list templates."""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Storage List
# ---------------------------------------------------------------------------


class StorageListRequest(BaseModel):
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

    storage_name: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox storage name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
            }
        }
    }


class StorageListItemReply(BaseModel):
    action: Literal["storage_list"]
    source: Literal["proxmox"]
    proxmox_node: str
    #
    storage_active: int
    storage_content_types: str
    storage_is_enable: int
    storage_is_share: int
    storage_name: str
    storage_space_available: int
    storage_space_total: int
    storage_space_used: int
    storage_space_used_fraction: float
    storage_type: str


class StorageListReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[StorageListItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "storage_list",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        #
                        "storage_active": 1,
                        "storage_content_types": "images,rootdir",
                        "storage_is_enable": 1,
                        "storage_is_share": 0,
                        "storage_name": "local-lvm",
                        "storage_space_available": 3600935440666,
                        "storage_space_total": 3836496314368,
                        "storage_space_used": 235560873702,
                        "storage_space_used_fraction": 0.0613999999999491,
                        "storage_type": "lvmthin",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Download ISO
# ---------------------------------------------------------------------------


class StorageDownloadIsoRequest(BaseModel):
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

    proxmox_storage: str = Field(
        ...,
        description="Target Proxmox storage name",
        pattern=r"^[A-Za-z0-9-]+$",
    )

    iso_file_content_type: str = Field(
        ...,
        description="MIME type of the ISO file",
        pattern=r"^[A-Za-z0-9-]*$",
        # pattern = r"^application/(?:x-)?iso9660-image$",
    )

    iso_file_name: str = Field(
        ...,
        description="ISO file name - must end with .iso",
        pattern=r"^[A-Za-z0-9._-]+\.iso$",
    )

    iso_url: str = Field(
        ...,
        description="http|https URL where the ISO will be downloaded from.",
        pattern=r"^https?://[^\s]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                #
                "iso_file_content_type": "iso",
                "iso_file_name": "ubuntu-24.04-live-server-amd64.iso",
                "iso_url": "https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso",
                "as_json": True,
            }
        }
    }


class StorageDownloadIsoItemReply(BaseModel):
    action: Literal["storage_download_iso"]
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


class StorageDownloadIsoReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[StorageDownloadIsoItemReply]

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
# List ISO
# ---------------------------------------------------------------------------


class StorageListIsoRequest(BaseModel):
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

    storage_name: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox storage name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
            }
        }
    }


class StorageListIsoItemReply(BaseModel):
    action: Literal["storage_list_iso"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    iso_content: str
    iso_ctime: int
    iso_format: str
    iso_size: int
    iso_vol_id: str
    local: str
    storage_name: str


class StorageListIsoReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[StorageListIsoItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "storage_list_iso",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "iso_content": "iso",
                        "iso_ctime": 1753343734,
                        "iso_format": "iso",
                        "iso_size": 614746112,
                        "iso_vol_id": "local:iso/noble-server-cloudimg-amd64.img",
                        "storage_name": "local",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# List Templates
# ---------------------------------------------------------------------------


class StorageListTemplateRequest(BaseModel):
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

    storage_name: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox storage name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
            }
        }
    }


class StorageListTemplateItemReply(BaseModel):
    action: Literal["storage_list_template"]
    source: Literal["proxmox"]
    proxmox_node: str

    # vm_id: int = Field(..., ge=1)
    storage_name: str
    template_content: str
    template_ctime: int
    template_format: str
    template_size: int
    template_vol_id: str


class StorageListTemplateReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[StorageListTemplateItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "storage_list_template",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        #
                        "storage_name": "local",
                        "template_content": "vztmpl",
                        "template_ctime": 1749734175,
                        "template_format": "tzst",
                        "template_size": 126515062,
                        "template_vol_id": "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# storage/list.py
Request_ProxmoxStorage_List = StorageListRequest
Reply_ProxmoxStorage_ListItem = StorageListItemReply
Reply_ProxmoxStorage_List = StorageListReply

# storage/download_iso.py
Request_ProxmoxStorage_DownloadIso = StorageDownloadIsoRequest
Reply_ProxmoxStorage_DownloadIsoItem = StorageDownloadIsoItemReply
Reply_ProxmoxStorage_DownloadIso = StorageDownloadIsoReply

# storage/storage_name/list_iso.py
Request_ProxmoxStorage_ListIso = StorageListIsoRequest
Reply_ProxmoxStorageWithStorageName_ListIsoItem = StorageListIsoItemReply
Reply_ProxmoxStorageWithStorageName_ListIso = StorageListIsoReply

# storage/storage_name/list_template.py
Request_ProxmoxStorage_ListTemplate = StorageListTemplateRequest
Reply_ProxmoxStorageWithStorageName_ListTemplateItem = StorageListTemplateItemReply
Reply_ProxmoxStorageWithStorageName_ListTemplate = StorageListTemplateReply
