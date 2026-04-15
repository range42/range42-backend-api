"""Proxmox host CRUD + health DTOs."""
from __future__ import annotations

from datetime import datetime

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
