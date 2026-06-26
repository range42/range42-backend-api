"""/v1/proxmox/hosts/{id}/vms/{vmid}/snapshots — per-VM PVE snapshots.

Direct PVE httpx. NET-NEW vs v0: supports vmtype=lxc and the vmstate flag
(v0 was qemu-only, no vmstate). create/delete/rollback return UPIDs.
"""
from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.v1.proxmox._helpers import (
    _assert_vmid_safe, _auth_headers, _get_host, _raise_for_pve, _session,
    _unreachable,
)
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import SnapshotItem, SnapshotCreateIn, VmActionResult

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


@router.post("/hosts/{host_id}/vms/{vmid}/snapshots", response_model=VmActionResult)
async def create_snapshot(
    host_id: str,
    vmid: int,
    body: SnapshotCreateIn,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    form: dict[str, str | int] = {"snapname": body.snapname}
    if body.description is not None:
        form["description"] = body.description
    # vmstate (save running RAM state) is a qemu-only PVE option; PVE rejects it
    # on /lxc/.../snapshot, so omit it for LXC (matches community.proxmox).
    if vmtype == "qemu" and body.vmstate is not None:
        form["vmstate"] = 1 if body.vmstate else 0
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.post(_snap_base(row, vmtype, vmid),
                               headers=_auth_headers(row), data=form)
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "snapshot create")
    return VmActionResult(status="accepted", upid=r.json().get("data"))


@router.delete(
    "/hosts/{host_id}/vms/{vmid}/snapshots/{name}", response_model=VmActionResult
)
async def delete_snapshot(
    host_id: str,
    vmid: int,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    name: str = Path(pattern=r"^[A-Za-z0-9_][A-Za-z0-9._-]*$"),
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    url = f"{_snap_base(row, vmtype, vmid)}/{name}"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.delete(url, headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "snapshot delete")
    return VmActionResult(status="accepted", upid=r.json().get("data"))


@router.post(
    "/hosts/{host_id}/vms/{vmid}/snapshots/{name}/rollback",
    response_model=VmActionResult,
)
async def rollback_snapshot(
    host_id: str,
    vmid: int,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    name: str = Path(pattern=r"^[A-Za-z0-9_][A-Za-z0-9._-]*$"),
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    _assert_vmid_safe(row, vmid, "rollback")
    url = f"{_snap_base(row, vmtype, vmid)}/{name}/rollback"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.post(url, headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "snapshot rollback")
    return VmActionResult(status="accepted", upid=r.json().get("data"))
