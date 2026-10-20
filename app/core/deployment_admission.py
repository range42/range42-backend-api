"""Close managed dispatch for exact retained deployment allocations.

This does not attest external/previous remote work, stop guests, lend capacity,
change identities, or pause a separately deployed workshop controller.
"""

from datetime import datetime, timezone
import json
import re
import uuid

from sqlalchemy import func, select, text

from app.core.allocation_models import DeploymentAllocation
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES
from app.core.config import Settings
from app.core.deployment_admission_models import DeploymentAdmission
from app.core.deployment_allocations import validate_binding
from app.core.errors import Range42Error
from app.core.locks import ProvisioningLock
from app.core.models import Attempt, Deployment, ProxmoxHost, WorkspaceLock
from app.core.physical_nodes import digest
from app.core.snapshot_models import SnapshotSet, SnapshotOperation


def refused(code="DEPLOYMENT_ADMISSION_REVIEW_REQUIRED"):
    messages = {
        "DEPLOYMENT_ADMISSION_REVIEW_REQUIRED": "The retained write-admission scope needs review. Preserve its state and restore the original owned allocation before changing it.",
        "DEPLOYMENT_ADMISSION_CHANGED": "The admission decision changed. Refresh it before making another deliberate request.",
        "DEPLOYMENT_WRITES_PAUSED": "Managed writes for this deployment are paused. An administrator must explicitly resume them before another operation.",
        "DEPLOYMENT_ADMISSION_DRAINING": "Accepted attempts or workspace locks remain. Reconcile them before resuming managed writes.",
    }
    return Range42Error(
        status=423 if code == "DEPLOYMENT_WRITES_PAUSED" else 409,
        code=code,
        message=messages[code],
    )


async def _deployment(session, deployment_id):
    row = await session.get(Deployment, deployment_id, populate_existing=True)
    if row is None:
        raise Range42Error(
            status=404, code="NOT_FOUND", message="Deployment not found."
        )
    return row


async def _scope(session, deployment_id):
    deployment = await _deployment(session, deployment_id)
    claim = await session.scalar(
        select(DeploymentAllocation)
        .where(DeploymentAllocation.deployment_id == deployment_id)
        .execution_options(populate_existing=True)
    )
    host = await session.get(
        ProxmoxHost, deployment.target_host_id, populate_existing=True
    )
    try:
        if claim is None:
            raise ValueError()
        validate_binding(claim, deployment, host)
        if (
            not isinstance(claim.assignments, list)
            or not 1 <= len(claim.assignments) <= 4096
        ):
            raise ValueError()
        vmids = [vm["vm_id"] for vm in claim.assignments]
        if any(
            type(vmid) is not int or not 100 <= vmid <= 999999999 for vmid in vmids
        ) or len(set(vmids)) != len(vmids):
            raise ValueError()
        return {
            "allocation_id": claim.id,
            "deployment_id": deployment_id,
            "host_id": claim.host_id,
            "api_url": claim.api_url,
            "node_name": claim.node_name,
            "project_sha": claim.project_sha,
            "assignments_digest": digest(claim.assignments),
            "vm_ids": sorted(vmids),
        }
    except (ValueError, KeyError, TypeError, Range42Error):
        raise refused() from None


def _valid(row):
    if (
        row.state not in {"open", "closed"}
        or type(row.revision) is not int
        or not 1 <= row.revision < 2**53
        or not isinstance(row.pause_id, str)
        or not re.fullmatch("[0-9a-f]{32}", row.pause_id)
        or not isinstance(row.last_request_id, str)
        or not re.fullmatch("[0-9a-f]{32}", row.last_request_id)
    ):
        raise refused()
    try:
        scope = json.loads(row.scope_json)
        if (
            digest(scope) != row.scope_digest
            or scope["deployment_id"] != row.deployment_id
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise refused() from None
    return scope


async def _closed_scopes(session):
    rows = (
        await session.scalars(
            select(DeploymentAdmission).execution_options(populate_existing=True)
        )
    ).all()
    result = []
    for row in rows:
        scope = _valid(row)
        if row.state == "closed":
            if scope != await _scope(session, row.deployment_id):
                raise refused()
            result.append((row, scope))
    return result


async def assert_deployment_admitted(session, deployment_id):
    for row, _ in await _closed_scopes(session):
        if row.deployment_id == deployment_id:
            raise refused("DEPLOYMENT_WRITES_PAUSED")


async def assert_vm_admitted(session, vmid):
    # VMIDs already have installation-wide exclusive allocator semantics.
    # Match IDs, not credentials/URLs, so another target alias cannot bypass it.
    for _, scope in await _closed_scopes(session):
        if vmid in scope["vm_ids"]:
            raise refused("DEPLOYMENT_WRITES_PAUSED")


async def snapshot(session, deployment_id):
    await _deployment(session, deployment_id)
    row = await session.get(DeploymentAdmission, deployment_id, populate_existing=True)
    active = await session.scalar(
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.deployment_id == deployment_id,
            Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
        )
    )
    locks = int(
        await session.get(WorkspaceLock, deployment_id, populate_existing=True)
        is not None
    )
    remote_snapshots = await session.scalar(
        select(func.count())
        .select_from(SnapshotOperation)
        .join(SnapshotSet, SnapshotSet.id == SnapshotOperation.set_id)
        .where(
            SnapshotSet.deployment_id == deployment_id,
            SnapshotOperation.state.in_({"running", "needs_review"}),
        )
    )
    if row:
        scope = _valid(row)
        if row.state == "closed" and scope != await _scope(session, deployment_id):
            raise refused()
        available = True
    else:
        try:
            scope = await _scope(session, deployment_id)
            available = True
        except Range42Error as error:
            if error.code != "DEPLOYMENT_ADMISSION_REVIEW_REQUIRED":
                raise
            available, scope = False, {"vm_ids": []}
    closed = row is not None and row.state == "closed"
    return {
        "scope": "managed_deployment_writers",
        "deployment_id": deployment_id,
        "available": available,
        "state": ("draining" if active or locks or remote_snapshots else "closed")
        if closed
        else "open",
        "revision": row.revision if row else 0,
        "pause_id": row.pause_id if closed else None,
        "vm_ids": scope["vm_ids"],
        "active_attempts": active,
        "held_workspace_locks": locks,
        "active_snapshot_operations": remote_snapshots,
        "changed_by": row.changed_by if row else None,
        "changed_at": row.changed_at if row else None,
        "remote_quiescence_verified": False,
        "capacity_borrowing_allowed": False,
    }


async def change(
    session,
    deployment_id,
    *,
    action,
    expected_revision,
    request_id,
    actor,
    pause_id=None,
):
    if (
        action not in {"pause", "resume"}
        or type(expected_revision) is not int
        or not 0 <= expected_revision < 2**53 - 1
        or not isinstance(request_id, str)
        or not re.fullmatch("[0-9a-f]{32}", request_id)
        or not isinstance(actor, str)
        or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", actor)
        or (action == "pause" and pause_id is not None)
        or (
            action == "resume"
            and (
                not isinstance(pause_id, str)
                or not re.fullmatch("[0-9a-f]{32}", pause_id)
            )
        )
    ):
        raise refused("DEPLOYMENT_ADMISSION_CHANGED")
    request_digest = digest(
        [deployment_id, action, expected_revision, request_id, pause_id]
    )
    # Serialize with all raw HTTP writers and detached runner dispatch. An
    # inherited live runner descriptor keeps this busy even across API failure.
    with ProvisioningLock(Settings().workspace_root / ".locks"):
        await session.rollback()
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            scope = await _scope(session, deployment_id)
            row = await session.get(
                DeploymentAdmission, deployment_id, populate_existing=True
            )
            if row:
                _valid(row)
                if row.state == "closed" and json.loads(row.scope_json) != scope:
                    raise refused()
                if row.last_request_digest == request_digest:
                    result = await snapshot(session, deployment_id)
                    await session.rollback()
                    return result
            if expected_revision != (row.revision if row else 0) or (
                row and row.last_request_id == request_id
            ):
                raise refused("DEPLOYMENT_ADMISSION_CHANGED")
            if action == "pause":
                if row and row.state != "open":
                    raise refused("DEPLOYMENT_ADMISSION_CHANGED")
                if row is None:
                    row = DeploymentAdmission(deployment_id=deployment_id, revision=0)
                    session.add(row)
                row.state, row.pause_id = "closed", uuid.uuid4().hex
                row.scope_json, row.scope_digest = (
                    json.dumps(scope, sort_keys=True),
                    digest(scope),
                )
            else:
                if row is None or row.state != "closed" or row.pause_id != pause_id:
                    raise refused("DEPLOYMENT_ADMISSION_CHANGED")
                if (await snapshot(session, deployment_id))["state"] == "draining":
                    raise refused("DEPLOYMENT_ADMISSION_DRAINING")
                row.state = "open"
            row.revision += 1
            row.last_request_id, row.last_request_digest = request_id, request_digest
            row.changed_by, row.changed_at = actor, datetime.now(timezone.utc)
            await session.flush()
            result = await snapshot(session, deployment_id)
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            raise
