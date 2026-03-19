"""Consolidated snapshot schemas: create, delete, list, revert."""

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Snapshot Create
# ---------------------------------------------------------------------------

class SnapshotCreateRequest(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to create",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    vm_snapshot_description: str | None = Field(
        default=None,
        description="Optional description for the snapshot",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_snapshot_name":"MY_VM_SNAPSHOT",
                "vm_snapshot_description":"MY_DESCRIPTION",
                "as_json": True
            }
        }
    }


class SnapshotCreateItemReply(BaseModel):

    action: Literal["vm_get_config"]
    proxmox_node: str
    source: Literal["proxmox"]
    vm_id: str # int = Field(..., ge=1)

    vm_name: str
    vm_snapshot_description: str
    vm_snapshot_name: str

    raw_data: str = Field(..., description="Raw string returned by proxmox")


class SnapshotCreateReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[SnapshotCreateItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "snapshot_vm_create",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_name": "admin-wazuh",
                        "vm_snapshot_description": "MY_DESCRIPTION",
                        "vm_snapshot_name": "MY_VM_SNAPSHOT",
                        "raw_data": { "data": "UPID:px-testing:002D5E30:1706941B:68C196E9:qmsnapshot:1000:API_master@pam!API_master:" }
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Snapshot Delete
# ---------------------------------------------------------------------------

class SnapshotDeleteRequest(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to delete",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_snapshot_name": "MY_VM_SNAPSHOT",
                "as_json": True
            }
        }
    }


class SnapshotDeleteItemReply(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class SnapshotDeleteReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[SnapshotDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                 "result": [
                    {
                        "action": "snapshot_vm_delete",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_name": "admin-wazuh",
                        "vm_snapshot_name": "BBBB",
                        "raw_data": { "data": "UPID:px-testing:002D6878:17077370:68C19925:qmdelsnapshot:1000:API_master@pam!API_master:"},
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Snapshot List
# ---------------------------------------------------------------------------

class SnapshotListRequest(BaseModel):

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
                "vm_id": "1111"
            }
        }
    }


class SnapshotListItemReply(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class SnapshotListReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[SnapshotListItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,

                "result": [
                    {
                        "action": "snapshot_vm_list",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_snapshot_description": "MY_DESCRIPTION",
                        "vm_snapshot_name": "MY_VM_SNAPSHOT",
                        "vm_snapshot_parent": "",
                        "vm_snapshot_time": 1757517545
                    },
                    {
                        "action": "snapshot_vm_list",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",

                        "vm_id": "1000",
                        "vm_snapshot_description": "You are here!",
                        "vm_snapshot_name": "current",
                        "vm_snapshot_parent": "MY_VM_SNAPSHOT",
                        "vm_snapshot_sha1": "7cc59c988bb8f18601fe076ad239f8b760667270"
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Snapshot Revert
# ---------------------------------------------------------------------------

class SnapshotRevertRequest(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to create",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "vm_id": "1111",
                "vm_snapshot_name":"CCCC",
                "as_json": True,
            }
        }
    }


class SnapshotRevertItemReply(BaseModel):

    action: Literal["vm_get_config"]
    source: Literal["proxmox"]
    # proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    # raw_data: str = Field(..., description="Raw string returned by proxmox")


class SnapshotRevertReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[SnapshotRevertItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "snapshot_vm_revert",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",

                        "vm_id": "1000",
                        "vm_name": "admin-wazuh",
                        "vm_snapshot_name": "CCCC",
                        "raw_data": {"data": "UPID:px-testing:002D7C57:17096777:68C19E25:qmrollback:1000:API_master@pam!API_master:"},
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# vm_id/snapshot/vm_create.py
Request_ProxmoxVmsVMID_CreateSnapshot = SnapshotCreateRequest
Reply_ProxmoxVmsVMID_CreateSnapshotItem = SnapshotCreateItemReply
Reply_ProxmoxVmsVMID_CreateSnapshot = SnapshotCreateReply

# vm_id/snapshot/vm_delete.py
Request_ProxmoxVmsVMID_DeleteSnapshot = SnapshotDeleteRequest
Reply_ProxmoxVmsVMID_DeleteSnapshotItem = SnapshotDeleteItemReply
Reply_ProxmoxVmsVMID_DeleteSnapshot = SnapshotDeleteReply

# vm_id/snapshot/vm_list.py
Request_ProxmoxVmsVMID_ListSnapshot = SnapshotListRequest
Reply_ProxmoxVmsVMID_ListSnapshotItem = SnapshotListItemReply
Reply_ProxmoxVmsVMID_ListSnapshot = SnapshotListReply

# vm_id/snapshot/vm_revert.py
Request_ProxmoxVmsVMID_RevertSnapshot = SnapshotRevertRequest
Reply_ProxmoxVmsVMID_RevertSnapshotItem = SnapshotRevertItemReply
Reply_ProxmoxVmsVMID_RevertSnapshot = SnapshotRevertReply
