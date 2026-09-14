"""/v1/deployments/:id/attempts — list + create."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES, finish_attempt
from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.events import EventsWriter
from app.core.locks import cleanup_stale_locks
from app.core.logging import get_logger
from app.core.models import Attempt, Deployment, WorkspaceLock
from app.core.scenario import validate_concrete_scope, validate_project_revision
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import AttemptCreate, AttemptOut

router = APIRouter()
log = get_logger(__name__)


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.get("/{deployment_id}/attempts", response_model=Page[AttemptOut])
async def list_attempts(deployment_id: str,
                        session: AsyncSession = Depends(_session)):
    rows = (await session.execute(
        select(Attempt).where(Attempt.deployment_id == deployment_id))).scalars().all()
    return Page[AttemptOut](
        items=[AttemptOut.model_validate(r, from_attributes=True) for r in rows],
        total=len(rows), offset=0, limit=len(rows),
    )


@router.post("/{deployment_id}/attempts", response_model=AttemptOut,
             status_code=status.HTTP_201_CREATED)
async def create_attempt(deployment_id: str, payload: AttemptCreate,
                         session: AsyncSession = Depends(_session)):
    return await reserve_attempt(deployment_id, payload, session)


async def reserve_attempt(deployment_id: str, payload: AttemptCreate,
                          session: AsyncSession, *, operation: dict | None = None):
    """Reserve all jobs through one lock predicate; runtime intent is server-owned."""
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    scope = "runtime" if operation is not None else payload.scope
    validate_concrete_scope(dep, scope)
    validate_project_revision(dep, scope, payload.project_sha)
    if payload.scope == "teardown":
        if payload.confirm_codename != dep.codename:
            raise Range42Error(
                error="confirm_mismatch", code="TEARDOWN_CONFIRM_MISMATCH", status=400,
                message="confirm_codename did not match deployment codename",
            )
        if dep.state == "unknown":
            raise Range42Error(
                error="teardown_blocked", code="DEPLOYMENT_UNKNOWN_STATE", status=409,
                message="Resolve the deployment's unknown state before teardown",
            )
    # Prior terminal attempts remain queryable. Reserve the current pointer
    # atomically so concurrent requests cannot replace an in-flight attempt.
    row = Attempt(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        scope=scope,
        operation=operation,
        project_sha=(payload.project_sha or dep.project_sha or "").lower() or None,
        team_id=payload.team_id,
        state="pending",
        started_at=datetime.now(timezone.utc),
    )
    session.add(row)
    active_attempt = select(Attempt.id).where(
        Attempt.id == Deployment.current_attempt_id,
        Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
    ).correlate(Deployment).exists()
    # Cancellation is terminal in the API before the subprocess finishes.
    # Its live lock still excludes replacement work during that shutdown.
    await cleanup_stale_locks(session, deployment_id=deployment_id)
    held_lock = select(WorkspaceLock.deployment_id).where(
        WorkspaceLock.deployment_id == Deployment.id,
    ).correlate(Deployment).exists()
    reserved = await session.execute(update(Deployment).where(
        Deployment.id == deployment_id, ~active_attempt, ~held_lock,
    ).values(current_attempt_id=row.id, state="pending"))
    if not reserved.rowcount:
        await session.rollback()
        raise Range42Error(
            error="attempt_in_progress", code="ATTEMPT_IN_PROGRESS", status=409,
            message="This deployment already has an active attempt or a runner still shutting down. "
                    "Wait for it to finish before retrying.",
        )
    await session.commit()
    await session.refresh(row)

    if os.getenv("RANGE42_AUTO_START_ATTEMPTS", "1") in ("1", "true", "yes"):
        attempt_id = row.id
        try:
            from app.core.deploy_trigger import start_attempt
            await start_attempt(session, attempt=row)
        except Exception as e:  # noqa: BLE001
            # Setup may leave the transaction failed and ORM objects expired.
            # Recover it before recording a durable, queryable terminal state.
            await session.rollback()
            code = e.code if isinstance(e, Range42Error) else "ATTEMPT_START_FAILED"
            # A broken workspace can cause both setup and event writing to fail.
            # Persist first; cancellation committed during checkout still wins.
            terminal_state = await finish_attempt(attempt_id=attempt_id, rc=None, error_code=code)
            await session.refresh(dep)
            try:
                cursor = EventsWriter(Path(dep.workspace_path) / "events.jsonl").append(
                    {"event_type": "attempt_end", "payload": {
                        "terminal_state": terminal_state, "code": code,
                        "message": ("Deployment setup stopped after cancellation." if terminal_state == "cancelled" else
                                    "Deployment setup failed before the runner started. "
                                    "Check the scenario and backend configuration, then retry."),
                        "rc": None,
                    }},
                    attempt_id=attempt_id, deployment_id=deployment_id,
                )
            except OSError as event_error:
                log.warning("attempt_failure_event_unavailable", attempt_id=attempt_id,
                            exception_type=type(event_error).__name__)
            else:
                await finish_attempt(attempt_id=attempt_id, rc=None, event_cursor_tip=cursor)
            await session.refresh(row)
            # Arbitrary exception text can contain Git credentials or vault data.
            log.warning("attempt_autostart_failed", attempt_id=attempt_id,
                        code=code, exception_type=type(e).__name__)
        else:
            # The runner records lifecycle updates in its own DB session.
            await session.refresh(row)

    return AttemptOut.model_validate(row, from_attributes=True)
