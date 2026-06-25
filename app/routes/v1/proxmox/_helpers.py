"""Shared helpers for the v1 proxmox route modules (vms/hosts/storage/snapshots).

Talk to PVE directly over httpx with a registered host's token. Extracted so
sibling modules reuse one copy instead of cross-importing privates.
"""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import AuthFailedError, Range42Error, VmidProtectedError
from app.core.logging import get_logger
from app.core.models import ProxmoxHost
from app.core.vmid_guard import VmidProtectedError as GuardVmidProtected
from app.core.vmid_guard import assert_vmid_safe

log = get_logger(__name__)


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


async def _get_host(host_id: str, session: AsyncSession) -> ProxmoxHost:
    row = (
        await session.execute(select(ProxmoxHost).where(ProxmoxHost.id == host_id))
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Host {host_id} not found",
        )
    return row


def _auth_headers(row: ProxmoxHost) -> dict[str, str]:
    return {"Authorization": f"PVEAPIToken={row.token_ref}"}


def _assert_vmid_safe(row: ProxmoxHost, vmid: int, action: str) -> None:
    overrides = (
        json.loads(row.protected_vmids_override_json)
        if row.protected_vmids_override_json else None
    )
    try:
        assert_vmid_safe(vmid, host_overrides=overrides)
    except GuardVmidProtected as e:
        raise VmidProtectedError(
            message=f"VMID {vmid} is protected; '{action}' is refused",
            details=e.details,
        ) from e


def _unreachable(row: ProxmoxHost, err: Exception) -> Range42Error:
    log.warning("proxmox unreachable", host=row.id, err=str(err))
    return Range42Error(
        error="upstream_error", code="PROXMOX_UNREACHABLE", status=502,
        message=f"Proxmox host {row.name} is unreachable",
        details=[{"field": "api_url", "reason": str(err)[:200]}],
    )


def _raise_for_pve(row: ProxmoxHost, r: httpx.Response, action: str = "request") -> None:
    """Map a non-200 PVE response to the canonical error classes."""
    if r.status_code in (401, 403):
        raise AuthFailedError(details=[{
            "field": "token_ref",
            "reason": f"Proxmox API rejected credentials ({r.status_code})",
        }])
    if r.status_code != 200:
        raise Range42Error(
            error="upstream_error", code="PROXMOX_ERROR", status=502,
            message=f"Proxmox returned {r.status_code} for {action}",
            details=[{"field": "host", "reason": (r.text or "")[:300]}],
        )
