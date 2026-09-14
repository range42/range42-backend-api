"""Shared helpers for the v1 proxmox route modules (vms/hosts/storage/snapshots).

Talk to PVE directly over httpx with a registered host's token. Extracted so
sibling modules reuse one copy instead of cross-importing privates.
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx
from fastapi import Depends
from sqlalchemy import select, text
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


async def _assert_snapshot_member_free(session: AsyncSession, vmid: int) -> None:
    from app.core.snapshot_models import SnapshotOperation, SnapshotSet
    members = await session.scalars(select(SnapshotSet.members).join(
        SnapshotOperation, SnapshotOperation.set_id == SnapshotSet.id
    ).where(SnapshotOperation.state.in_({"running", "needs_review"})))
    for rows in members:
        if (not isinstance(rows, list) or any(not isinstance(row, dict) or type(row.get("vm_id")) is not int for row in rows)
                or any(row["vm_id"] == vmid for row in rows)):
            raise Range42Error(status=409, code="SNAPSHOT_MEMBER_BUSY", error="snapshot_member_busy",
                               message="A snapshot-set operation owns this guest. Reconcile it before another mutation.")


async def _mutation_session(vmid: int, session: AsyncSession = Depends(_session)) -> AsyncSession:
    """Fence raw VM writes against finite set dispatch and durable remote work.

    Internal snapshot dispatch calls the primitive with its already-held
    session; HTTP callers cannot bypass this dependency through request data.
    """
    from app.core.config import Settings
    from app.core.locks import ProvisioningLock
    with ProvisioningLock(Settings().workspace_root / ".locks"):
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
            await _assert_snapshot_member_free(session, vmid)
            yield session
        finally:
            await session.rollback()


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


def _config_target_digest(row: ProxmoxHost, vmid: int, vmtype: str) -> str:
    """Stable across guest config changes, invalidated by target re-registration.

    Include credential/protection changes without exposing either value. This
    is a context guard, not authorization or a durable task ownership record.
    """
    binding = (row.id, row.api_url.rstrip("/"), row.node_name, row.token_ref,
               row.protected_vmids_override_json, vmid, vmtype)
    return hashlib.sha256(json.dumps(binding, separators=(",", ":")).encode()).hexdigest()


def _config_task_vmid(upid: str, node: str) -> int | None:
    """Only the QEMU configuration worker has this guarded polling contract."""
    if not isinstance(upid, str) or len(upid) > 512:
        return None
    match = re.fullmatch(
        rf"UPID:{re.escape(node)}:[A-Fa-f0-9]+:[A-Fa-f0-9]+:[A-Fa-f0-9]+:qmconfig:([0-9]{{3,9}}):[^:\s]+:", upid,
    )
    if match and 100 <= int(match[1]) <= 999999999:
        return int(match[1])
    return None


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
