"""Persist runner state using sessions independent of the initiating request."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, or_, update

from app.core.db import get_session_factory
from app.core.locks import heartbeat, release_lock
from app.core.models import Attempt, Deployment

TERMINAL_ATTEMPT_STATES = ("succeeded", "completed", "partial", "failed", "cancelled", "unknown")


async def advance_attempt_cursor(*, attempt_id: str, event_cursor_tip: int) -> None:
    """Publish observed canonical events without changing lifecycle or ownership."""
    async with get_session_factory()() as session:
        await session.execute(update(Attempt).where(
            Attempt.id == attempt_id, Attempt.event_cursor_tip < event_cursor_tip,
        ).values(event_cursor_tip=event_cursor_tip))
        await session.commit()


async def heartbeat_attempt(*, attempt_id: str, deployment_id: str) -> bool:
    """Renew this process's lock in an independent, short-lived transaction."""
    async with get_session_factory()() as session:
        renewed = await heartbeat(session, deployment_id=deployment_id,
                                  owner=f"attempt-{attempt_id}")
        await session.commit()
        return renewed


async def keep_attempt_lock(*, attempt_id: str, deployment_id: str,
                            stop: asyncio.Event, interval_s: float = 30) -> None:
    """Maintain ownership until process shutdown, stop, or loss of the lock.

    The caller owns this task and must await it when stopping the runner. A DB
    failure propagates to that caller rather than silently expiring the lock.
    """
    while not stop.is_set():
        if not await heartbeat_attempt(attempt_id=attempt_id, deployment_id=deployment_id):
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def mark_attempt_running(*, attempt_id: str, pid: int | None,
                               artifact_dir: str | Path) -> bool:
    """Record a spawned process; return False if the attempt is already terminal.

    The caller must stop any spawned process when this returns False. This
    handles cancellation between the request creating an attempt and spawning
    the runner without resurrecting a cancelled attempt.
    """
    async with get_session_factory()() as session:
        result = await session.execute(update(Attempt).where(
            Attempt.id == attempt_id, Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
            or_(Attempt.sub_reason.is_(None), Attempt.sub_reason != "cancel_requested"),
        ).values(state="deploying", pid=pid, artifact_dir=str(artifact_dir),
                 started_at=datetime.now(timezone.utc)))
        if not result.rowcount:
            return False
        attempt = await session.get(Attempt, attempt_id)
        await session.execute(update(Deployment).where(
            Deployment.id == attempt.deployment_id,
            Deployment.current_attempt_id == attempt_id,
        ).values(state="deploying"))
        await session.commit()
        return True


async def finish_attempt(*, attempt_id: str, rc: int | None,
                         event_cursor_tip: int | None = None,
                         error_code: str | None = None,
                         warning_code: str | None = None,
                         cancelled: bool = False,
                         unknown: bool = False,
                         partial: bool = False) -> str | None:
    """Persist completion and release this attempt's lock; return its final state.

    Explicit cancellation and previously persisted terminal results win over
    late subprocess callbacks. The function is idempotent, so the caller may
    call it again after emitting attempt_end to advance event_cursor_tip.
    Only a numeric process result belongs in rc; watcher failures use a stable
    error_code and rc=None. Missing attempts return None without recreating data.
    """
    terminal_state = "cancelled" if cancelled else "unknown" if unknown else "partial" if partial else (
        "succeeded" if rc == 0 and error_code is None else "failed"
    )
    async with get_session_factory()() as session:
        # The predicate also protects cancellation committed concurrently by
        # the HTTP endpoint, before this background callback gets its write lock.
        await session.execute(update(Attempt).where(
            Attempt.id == attempt_id, Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
        ).values(state=case((Attempt.sub_reason == "cancel_requested", "cancelled"), else_=terminal_state),
                 rc=rc, sub_reason=case((Attempt.sub_reason == "cancel_requested", "cancel_requested"),
                                      else_=error_code or warning_code),
                 ended_at=datetime.now(timezone.utc)))
        attempt = await session.get(Attempt, attempt_id)
        if attempt is None:
            return None
        # Cancellation can be recorded before the process exits; retain that
        # decision and timestamp while adding its real return code afterwards.
        if attempt.rc is None and rc is not None:
            attempt.rc = rc
        if event_cursor_tip is not None:
            await session.execute(update(Attempt).where(Attempt.id == attempt_id).values(
                event_cursor_tip=case(
                    (Attempt.event_cursor_tip < event_cursor_tip, event_cursor_tip),
                    else_=Attempt.event_cursor_tip,
                ),
            ))
        await session.execute(update(Deployment).where(
            Deployment.id == attempt.deployment_id,
            Deployment.current_attempt_id == attempt_id,
        ).values(state=attempt.state))
        await release_lock(session, deployment_id=attempt.deployment_id,
                           owner=f"attempt-{attempt_id}")
        await session.commit()
        return attempt.state
