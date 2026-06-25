"""Proxmox host CRUD + health DTOs."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl


class HostIn(BaseModel):
    name: str
    api_url: HttpUrl
    node_name: str
    token_ref: str
    token_scope: str | None = None
    default_bridge: str = "vmbr0"
    protected_vmids_override: list[list[int]] | None = None


class HostOut(HostIn):
    id: str
    added_at: datetime
    last_health_check: dict | None = None


class HostHealth(BaseModel):
    status: str
    rtt_ms: int | None = None
    sdn_available: bool | None = None
    at: datetime


class VmSummary(BaseModel):
    """A qemu VM or LXC container on a registered host's node."""

    vmid: int
    name: str | None = None
    type: str  # "qemu" | "lxc"
    status: str  # "running" | "stopped" | "paused" | ...
    node: str
    maxmem: int | None = None
    maxcpu: float | None = None
    uptime: int | None = None
    template: bool = False
    tags: str | None = None


class VmActionResult(BaseModel):
    status: str = "accepted"
    upid: str | None = None


class TaskStatus(BaseModel):
    upid: str
    status: Literal["running", "stopped"]
    exitstatus: str | None = None
    node: str


class StoragePool(BaseModel):
    storage: str
    type: str
    content: str | None = None
    total: int | None = None
    used: int | None = None
    avail: int | None = None
    active: bool | None = None


class StorageContent(BaseModel):
    """A volume in a storage pool. ``name`` is derived from ``volid`` (PVE does
    not return it) and is load-bearing for the UI's TemplateBrowser."""
    volid: str
    name: str
    content: str
    size: int | None = None
    format: str | None = None
    vmid: int | None = None


class DownloadUrlIn(BaseModel):
    content: Literal["iso", "vztmpl"]
    filename: str
    url: str
    checksum: str | None = None
    checksum_algorithm: str | None = None
