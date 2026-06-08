"""/v1/proxmox/hosts/{id}/vms — list + lifecycle.

Talks to the Proxmox API directly over httpx using the registered host's token
(single source of truth). Replaces the v0 Ansible-driven proxmox surface for
VM listing and start/stop/pause/resume.
"""
from __future__ import annotations

import json
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import AuthFailedError, Range42Error, VmidProtectedError
from app.core.logging import get_logger
from app.core.models import ProxmoxHost
from app.core.vmid_guard import VmidProtectedError as GuardVmidProtected
from app.core.vmid_guard import assert_vmid_safe
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import TaskStatus, VmActionResult, VmSummary

router = APIRouter()
log = get_logger(__name__)

# Proxmox status sub-actions we expose. `resume`/`start` are non-destructive;
# the rest can disrupt a running guest.
_ALLOWED_ACTIONS = {"start", "stop", "shutdown", "suspend", "resume", "reboot"}
_DESTRUCTIVE_ACTIONS = {"stop", "shutdown", "suspend", "reboot"}


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


async def _get_host(host_id: str, session: AsyncSession) -> ProxmoxHost:
    row = (
        await session.execute(
            select(ProxmoxHost).where(ProxmoxHost.id == host_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Host {host_id} not found",
        )
    return row


def _auth_headers(row: ProxmoxHost) -> dict[str, str]:
    return {"Authorization": f"PVEAPIToken={row.token_ref}"}


def _assert_vmid_safe(row: ProxmoxHost, vmid: int, action: str) -> None:
    """Refuse destructive actions on protected VMIDs (canonical ranges + per-host
    overrides). `action` only feeds the error message."""
    overrides = (
        json.loads(row.protected_vmids_override_json)
        if row.protected_vmids_override_json
        else None
    )
    try:
        assert_vmid_safe(vmid, host_overrides=overrides)
    except GuardVmidProtected as e:
        raise VmidProtectedError(
            message=f"VMID {vmid} is protected; '{action}' is refused",
            details=e.details,
        ) from e


@router.get("/hosts/{host_id}/vms", response_model=Page[VmSummary])
async def list_host_vms(host_id: str, session: AsyncSession = Depends(_session)):
    row = await _get_host(host_id, session)
    base = row.api_url.rstrip("/")
    headers = _auth_headers(row)
    items: list[VmSummary] = []
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            for vm_type in ("qemu", "lxc"):
                r = await cli.get(
                    f"{base}/api2/json/nodes/{row.node_name}/{vm_type}", headers=headers
                )
                if r.status_code in (401, 403):
                    raise AuthFailedError(
                        details=[{
                            "field": "token_ref",
                            "reason": f"Proxmox API rejected credentials ({r.status_code})",
                        }]
                    )
                if r.status_code != 200:
                    # A node may not have one guest type; skip rather than fail.
                    continue
                for v in r.json().get("data", []):
                    items.append(VmSummary(
                        vmid=v["vmid"],
                        name=v.get("name"),
                        type=vm_type,
                        status=v.get("status", "unknown"),
                        node=row.node_name,
                        maxmem=v.get("maxmem"),
                        maxcpu=v.get("maxcpu") or v.get("cpus"),
                        uptime=v.get("uptime"),
                        template=bool(v.get("template", 0)),
                        tags=v.get("tags"),
                    ))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    return Page[VmSummary](
        items=items, total=len(items), offset=0, limit=len(items)
    )


@router.post(
    "/hosts/{host_id}/vms/{vmid}/status/{action}", response_model=VmActionResult
)
async def vm_status_action(
    host_id: str,
    vmid: int,
    action: str,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    session: AsyncSession = Depends(_session),
):
    if action not in _ALLOWED_ACTIONS:
        raise Range42Error(
            error="validation_error",
            code="INVALID_ACTION",
            status=400,
            message=f"Unsupported action '{action}'",
            details=[{"field": "action", "reason": f"one of {sorted(_ALLOWED_ACTIONS)}"}],
        )
    row = await _get_host(host_id, session)
    if action in _DESTRUCTIVE_ACTIONS:
        _assert_vmid_safe(row, vmid, action)
    base = row.api_url.rstrip("/")
    url = f"{base}/api2/json/nodes/{row.node_name}/{vmtype}/{vmid}/status/{action}"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.post(url, headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    if r.status_code in (401, 403):
        raise AuthFailedError(
            details=[{
                "field": "token_ref",
                "reason": f"Proxmox API rejected credentials ({r.status_code})",
            }]
        )
    if r.status_code != 200:
        raise Range42Error(
            error="upstream_error",
            code="PROXMOX_ERROR",
            status=502,
            message=f"Proxmox returned {r.status_code} for {action}",
            details=[{"field": "vmid", "reason": r.text[:300]}],
        )
    return VmActionResult(status="accepted", upid=r.json().get("data"))


def _unreachable(row: ProxmoxHost, err: Exception) -> Range42Error:
    log.warning("proxmox unreachable", host=row.id, err=str(err))
    return Range42Error(
        error="upstream_error",
        code="PROXMOX_UNREACHABLE",
        status=502,
        message=f"Proxmox host {row.name} is unreachable",
        details=[{"field": "api_url", "reason": str(err)[:200]}],
    )


@router.delete("/hosts/{host_id}/vms/{vmid}", response_model=VmActionResult)
async def vm_delete(
    host_id: str,
    vmid: int,
    vmtype: Literal["qemu", "lxc"] = "qemu",
    purge: bool = True,
    session: AsyncSession = Depends(_session),
):
    row = await _get_host(host_id, session)
    _assert_vmid_safe(row, vmid, "delete")
    base = row.api_url.rstrip("/")
    url = f"{base}/api2/json/nodes/{row.node_name}/{vmtype}/{vmid}"
    params: dict[str, int] = {}
    if purge:
        params["purge"] = 1
        params["destroy-unreferenced-disks"] = 1
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.delete(url, headers=_auth_headers(row), params=params)
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    if r.status_code in (401, 403):
        raise AuthFailedError(
            details=[{"field": "token_ref", "reason": f"Proxmox API rejected credentials ({r.status_code})"}]
        )
    if r.status_code != 200:
        text = r.text or ""
        if "running" in text.lower() or "stop it first" in text.lower():
            raise Range42Error(
                error="conflict",
                code="VM_RUNNING",
                status=409,
                message="Stop the VM before deleting",
                details=[{"field": "vmid", "reason": text[:300]}],
            )
        raise Range42Error(
            error="upstream_error",
            code="PROXMOX_ERROR",
            status=502,
            message=f"Proxmox returned {r.status_code} for delete",
            details=[{"field": "vmid", "reason": text[:300]}],
        )
    return VmActionResult(status="accepted", upid=r.json().get("data"))


@router.get("/hosts/{host_id}/tasks/{upid:path}/status", response_model=TaskStatus)
async def task_status(host_id: str, upid: str, session: AsyncSession = Depends(_session)):
    row = await _get_host(host_id, session)
    base = row.api_url.rstrip("/")
    enc = quote(upid, safe="")
    url = f"{base}/api2/json/nodes/{row.node_name}/tasks/{enc}/status"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as cli:
            r = await cli.get(url, headers=_auth_headers(row))
    except httpx.RequestError as e:
        raise _unreachable(row, e) from e
    if r.status_code in (401, 403):
        raise AuthFailedError(
            details=[{"field": "token_ref", "reason": f"Proxmox API rejected credentials ({r.status_code})"}]
        )
    if r.status_code != 200:
        raise Range42Error(
            error="upstream_error",
            code="PROXMOX_ERROR",
            status=502,
            message=f"Proxmox returned {r.status_code} for task status",
            details=[{"field": "upid", "reason": (r.text or "")[:300]}],
        )
    data = r.json().get("data", {}) or {}
    return TaskStatus(
        upid=upid,
        status=data.get("status", "running"),
        exitstatus=data.get("exitstatus"),
        node=row.node_name,
    )
