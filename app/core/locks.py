"""Deployment-scoped lock with heartbeat.

Stale after 3 * heartbeat_interval_s. Cleanup runs both on acquire() (if
a stale lock exists, it is evicted in the same transaction) and from
apscheduler every interval.

Spec refs: §7 concurrency, §8 concurrency primitives.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Range42Error
from app.core.models import WorkspaceLock


class LockHeldError(Range42Error):
    status = 409
    error = "deployment_locked"
    code = "DEPLOYMENT_LOCKED"


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


async def cleanup_stale_locks(session: AsyncSession) -> int:
    rows = (await session.execute(select(WorkspaceLock))).scalars().all()
    stale = [r for r in rows if _is_stale(r)]
    for r in stale:
        await session.delete(r)
    return len(stale)
