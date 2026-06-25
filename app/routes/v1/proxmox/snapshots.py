"""/v1/proxmox/hosts/{id}/vms/{vmid}/snapshots — per-VM PVE snapshots.

Direct PVE httpx. NET-NEW vs v0: supports vmtype=lxc and the vmstate flag
(v0 was qemu-only, no vmstate). create/delete/rollback return UPIDs.
"""
from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.v1.proxmox._helpers import (
    _assert_vmid_safe, _auth_headers, _get_host, _raise_for_pve, _session,
    _unreachable,
)
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import SnapshotItem, VmActionResult

router = APIRouter()


def _snap_base(row, vmtype: str, vmid: int) -> str:
    return (
        f"{row.api_url.rstrip('/')}/api2/json/nodes/{row.node_name}"
        f"/{vmtype}/{vmid}/snapshot"
    )


@router.get(
    "/hosts/{host_id}/vms/{vmid}/snapshots", response_model=Page[SnapshotItem]
)
async def list_snapshots(
    host_id: str,
    vmid: int,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.get(_snap_base(row, vmtype, vmid), headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "snapshot list")
    data = r.json().get("data", []) or []
    items = [
        SnapshotItem(
            name=d["name"], description=d.get("description"),
            snaptime=d.get("snaptime"),
            vmstate=bool(d["vmstate"]) if d.get("vmstate") is not None else None,
            parent=d.get("parent"),
        )
        for d in data
        if d.get("name") and d["name"] != "current"
    ]
    return Page(items=items, total=len(items))
