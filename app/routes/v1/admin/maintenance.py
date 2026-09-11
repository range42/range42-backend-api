"""Read-only proof for an operator's container maintenance admission lock."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.errors import Range42Error


class ProcessIdentity(BaseModel):
    pid: int
    start_time: str
    boot_id: str


class MaintenanceLockIdentity(BaseModel):
    path: str
    device: int
    inode: int
    uid: int


class MaintenanceCapability(BaseModel):
    protocol: Literal["flock-http-v1"]
    enabled: bool
    process: ProcessIdentity
    lock: MaintenanceLockIdentity | None


router = APIRouter()


@router.get("/maintenance", response_model=MaintenanceCapability)
async def maintenance_capability(request: Request):
    try:
        return request.app.state.maintenance_gate.capability()
    except (OSError, ValueError):
        raise Range42Error(status=503, error="maintenance_gate_unavailable", code="MAINTENANCE_GATE_UNAVAILABLE",
                           message="The maintenance gate cannot prove its configured process and lock identity.") from None
