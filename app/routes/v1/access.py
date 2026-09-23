"""Authenticated identity and administrator-only mutation audit readback."""
from typing import Literal
from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core import audit
from app.core.access import Principal

router = APIRouter()


class IdentityOut(BaseModel):
    actor_id: str
    role: Literal["admin", "operator", "viewer"]
    scope: Literal["installation"] = "installation"
    audit_enabled: bool


class AuditItem(BaseModel):
    id: str
    actor_id: str
    role: str
    method: str
    route: str
    state: str
    status_code: int | None
    created_at: datetime
    finished_at: datetime | None


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int


@router.get("/auth/me", response_model=IdentityOut)
async def identity(request: Request):
    principal = getattr(request.state, "principal", Principal("development", "admin"))
    return IdentityOut(actor_id=principal.actor_id, role=principal.role,
                       audit_enabled=getattr(request.state, "audit_enabled", False))


@router.get("/admin/audit", response_model=AuditPage)
async def audit_records(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return await audit.list_records(offset, limit)
