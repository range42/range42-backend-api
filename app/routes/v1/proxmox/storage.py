"""/v1/proxmox/hosts/{id}/storage — pools + content listing + download-url.

Direct PVE httpx. Replaces the v0 Ansible-driven storage surface.
"""
from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.v1.proxmox._helpers import (
    _auth_headers, _get_host, _raise_for_pve, _session, _unreachable,
)
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import StoragePool

router = APIRouter()


@router.get("/hosts/{host_id}/storage", response_model=Page[StoragePool])
async def list_storage(host_id: str, session: AsyncSession = Depends(_session)):
    row = await _get_host(host_id, session)
    url = f"{row.api_url.rstrip('/')}/api2/json/nodes/{row.node_name}/storage"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.get(url, headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "storage list")
    data = r.json().get("data", []) or []
    items = [
        StoragePool(
            storage=d.get("storage"), type=d.get("type"), content=d.get("content"),
            total=d.get("total"), used=d.get("used"), avail=d.get("avail"),
            active=bool(d["active"]) if d.get("active") is not None else None,
        )
        for d in data
    ]
    return Page(items=items, total=len(items))
