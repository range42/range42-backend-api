"""Deployment-scoped lock with heartbeat.

Stale after 3 * heartbeat_interval_s. Cleanup runs both on acquire() (if
a stale lock exists, it is evicted in the same transaction) and from
apscheduler every interval.

Spec refs: §7 concurrency, §8 concurrency primitives.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import fcntl
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Range42Error
from app.core.models import WorkspaceLock


class LockHeldError(Range42Error):
    status = 409
    error = "deployment_locked"
    code = "DEPLOYMENT_LOCKED"


class ProvisioningLock:
    """Serialize full provisioning across this installation's workspaces.

    The detached runner inherits the locked descriptor. A hard API crash cannot
    release the lock while that runner is staging/applying cluster SDN changes.
    All targets share one lock so aliases for the same cluster cannot bypass it.
    External Proxmox writers must still coordinate their changes separately.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.fd = -1

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory / "provisioning.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Range42Error(
                status=409, error="provisioning_busy", code="PROVISIONING_BUSY",
                message="Another full deployment is preparing infrastructure. Wait for it to finish before retrying.",
            ) from None
        self.fd = fd
        return self

    def __exit__(self, *_):
        # Do not LOCK_UN: that would also unlock the child's inherited open
        # file description. Closing leaves it locked until the runner exits.
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _is_stale(lock: WorkspaceLock) -> bool:
    now = datetime.now(timezone.utc)
    hb = lock.heartbeat_at
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    return (now - hb) > timedelta(seconds=3 * lock.heartbeat_interval_s)


async def acquire_lock(
    session: AsyncSession,
    *,
    deployment_id: str,
    owner: str,
    interval_s: int,
) -> WorkspaceLock:
    existing = (
        await session.execute(
            select(WorkspaceLock).where(
                WorkspaceLock.deployment_id == deployment_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if _is_stale(existing):
            await session.delete(existing)
            await session.flush()
        else:
            raise LockHeldError(
                details=[
                    {
                        "field": "deployment_id",
                        "reason": (
                            f"held by {existing.owner} since "
                            f"{existing.acquired_at.isoformat()}"
                        ),
                    }
                ]
            )
    lock = WorkspaceLock(
        deployment_id=deployment_id,
        owner=owner,
        heartbeat_interval_s=interval_s,
    )
    session.add(lock)
    return lock


async def release_lock(
    session: AsyncSession, *, deployment_id: str, owner: str
) -> bool:
    existing = (
        await session.execute(
            select(WorkspaceLock).where(
                WorkspaceLock.deployment_id == deployment_id
            )
        )
    ).scalar_one_or_none()
    if existing is None or existing.owner != owner:
        return False
    await session.delete(existing)
    return True


async def heartbeat(
    session: AsyncSession, *, deployment_id: str, owner: str
) -> bool:
    existing = (
        await session.execute(
            select(WorkspaceLock).where(
                WorkspaceLock.deployment_id == deployment_id
            )
        )
    ).scalar_one_or_none()
    if existing is None or existing.owner != owner:
        return False
    existing.heartbeat_at = datetime.now(timezone.utc)
    return True


async def cleanup_stale_locks(session: AsyncSession, *, deployment_id: str | None = None) -> int:
    query = select(WorkspaceLock)
    if deployment_id is not None:
        query = query.where(WorkspaceLock.deployment_id == deployment_id)
    rows = (await session.execute(query)).scalars().all()
    stale = [r for r in rows if _is_stale(r)]
    for r in stale:
        await session.delete(r)
    return len(stale)
