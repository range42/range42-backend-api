"""/v1/proxmox/hosts/{id}/storage — pools + content listing + download-url.

Direct PVE httpx. Replaces the v0 Ansible-driven storage surface.
"""
from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.routes.v1.proxmox._helpers import (
    _auth_headers, _get_host, _raise_for_pve, _session, _unreachable,
)
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import DownloadUrlIn, StorageContent, StoragePool, VmActionResult

router = APIRouter()


def _volid_name(volid: str) -> str:
    """'local:iso/ubuntu.iso' -> 'ubuntu.iso'; 'local:vztmpl/x.tar.zst' -> 'x.tar.zst'."""
    if "/" in volid:
        return volid.rsplit("/", 1)[-1]
    return volid.rsplit(":", 1)[-1]


@router.get(
    "/hosts/{host_id}/storage/{store}/content",
    response_model=Page[StorageContent],
)
async def list_storage_content(
    host_id: str,
    content: Literal["iso", "vztmpl"] = "iso",
    store: str = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    url = (
        f"{row.api_url.rstrip('/')}/api2/json/nodes/{row.node_name}"
        f"/storage/{store}/content"
    )
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.get(url, headers=_auth_headers(row),
                              params={"content": content})
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "storage content")
    data = r.json().get("data", []) or []
    items = [
        StorageContent(
            volid=d["volid"], name=_volid_name(d["volid"]),
            content=d.get("content", content), size=d.get("size"),
            format=d.get("format"), vmid=d.get("vmid"),
        )
        for d in data
    ]
    return Page(items=items, total=len(items))


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


@router.post(
    "/hosts/{host_id}/storage/{store}/download-url",
    response_model=VmActionResult,
)
async def storage_download_url(
    host_id: str,
    body: DownloadUrlIn,
    store: str = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    url = (
        f"{row.api_url.rstrip('/')}/api2/json/nodes/{row.node_name}"
        f"/storage/{store}/download-url"
    )
    form: dict[str, str] = {
        "content": body.content, "filename": body.filename, "url": body.url,
    }
    if body.checksum is not None:
        form["checksum"] = body.checksum
    if body.checksum_algorithm is not None:
        form["checksum-algorithm"] = body.checksum_algorithm
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.post(url, headers=_auth_headers(row), data=form)
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    _raise_for_pve(row, r, "download-url")
    return VmActionResult(status="accepted", upid=r.json().get("data"))
