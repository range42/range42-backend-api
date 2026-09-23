"""Shared submission path for deployment and scoped lifecycle operations."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import deploy_trigger
from app.core.errors import Range42Error, RunnerSetupError
from app.core.locks import acquire_lock, release_lock
from app.core.models import Attempt, Deployment


async def submit_attempt(session: AsyncSession, dep: Deployment, *, scope: str,
                         team_id: int | None = None,
                         operation_vars: dict[str, Any] | None = None) -> Attempt:
    if team_id is not None and not 1 <= team_id <= dep.team_count:
        raise Range42Error(code="TEAM_OUT_OF_RANGE", status=400, message="Invalid team_id")
    if scope in {"team_reset", "rollback_team", "snapshot_team"} and team_id is None:
        raise Range42Error(code="TEAM_REQUIRED", status=400, message="team_id is required")
    if scope == "teardown" and dep.state == "unknown":
        raise Range42Error(code="DEPLOYMENT_UNKNOWN_STATE", status=409,
                           message="Resolve the unknown deployment state before teardown")
    # No operation may silently fall back to the provisioning playbook.
    if scope != "full":
        deploy_trigger.resolve_attempt_playbook(dep.scenario_label, scope)
    row = Attempt(id=uuid.uuid4().hex[:16], deployment_id=dep.id, scope=scope,
                  team_id=team_id, state="pending", started_at=datetime.now(timezone.utc))
    owner = f"attempt-{row.id}"
    await acquire_lock(session, deployment_id=dep.id, owner=owner, interval_s=30)
    session.add(row)
    dep.current_attempt_id = row.id
    dep.state = "pending"
    await session.flush()
    if os.getenv("RANGE42_AUTO_START_ATTEMPTS", "1").lower() not in {"1", "true", "yes"}:
        await release_lock(session, deployment_id=dep.id, owner=owner)
        await session.commit()
        return row
    await session.commit()
    try:
        await deploy_trigger.start_attempt(session, attempt=row, lock_acquired=True,
                                           operation_vars=operation_vars)
    except Range42Error:
        raise
    except Exception as exc:
        raise RunnerSetupError(message="Attempt could not start; see its persisted failure state") from exc
    await session.refresh(row)
    return row
