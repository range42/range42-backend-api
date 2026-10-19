"""Durable finite native snapshot dispatch with read-only task recovery.

No multi-VM atomicity, automatic compensation or repeat of an ambiguous
dispatch. Every potentially live remote worker retains a durable Attempt.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import quote

import httpx
from sqlalchemy import func, select, text, update

from app.core import db
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES
from app.core.config import Settings
from app.core.deployment_allocations import manifest_assignments
from app.core.errors import Range42Error
from app.core.locks import ProvisioningLock, acquire_lock, release_lock
from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.proxmox_read import read_proxmox_data, ProxmoxReadError
from app.core.proxmox_tls import proxmox_verify
from app.core.scenario import prepare_project_scenario
from app.core.snapshot_models import SnapshotSet, SnapshotOperation

ACTIVE = {"running", "needs_review"}


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def blocked(message, code="SNAPSHOT_PLAN_STALE", status=409):
    return Range42Error(
        status=status, code=code, error="snapshot_operation_blocked", message=message
    )


def target_digest(host):
    return digest(
        [
            host.id,
            host.api_url.rstrip("/"),
            host.node_name,
            host.token_ref,
            host.protected_vmids_override_json,
        ]
    )


async def _deployment(session, deployment_id):
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise blocked("Deployment not found.", "NOT_FOUND", 404)
    if not deployment.project_sha or deployment.scenario_label == "_universal":
        raise blocked(
            "Snapshot sets require a pinned concrete deployment.",
            "SNAPSHOT_SCENARIO_UNSUPPORTED",
        )
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    if host is None:
        raise blocked(
            "The deployment target is unavailable.", "SNAPSHOT_TARGET_CHANGED"
        )
    return deployment, host


def _binding(snapshot, deployment, host):
    if (
        snapshot.deployment_id != deployment.id
        or snapshot.project_sha != deployment.project_sha
        or snapshot.host_id != deployment.target_host_id
        or snapshot.target_digest != target_digest(host)
    ):
        raise blocked(
            "Deployment revision or registered target credentials changed; restore the original binding before recovery.",
            "SNAPSHOT_TARGET_CHANGED",
        )


async def _read(client, host, path, params=None):
    try:
        return await read_proxmox_data(client, host, path, params=params)
    except ProxmoxReadError:
        raise blocked(
            "Cannot verify snapshot ownership or native task state.",
            "SNAPSHOT_READ_UNAVAILABLE",
        ) from None


def _base(host, vmid):
    return f"/nodes/{quote(host.node_name, safe='')}/qemu/{vmid}"


def _config_digest(config):
    # Parent metadata changes after snapshot creation; vmgenid can change on
    # rollback. Neither is a clone identity or guest-content proof.
    return digest(
        {
            # Current config masks cipassword; snapshot config returns its hash.
            # Compare the same representation, preserving whether it is present
            # and compatibility with reviews saved from the masked current API.
            key: "**********" if key == "cipassword" else value
            for key, value in config.items()
            if key
            not in {
                "digest",
                "parent",
                "snaptime",
                "snapstate",
                "vmstate",
                "vmgenid",
                "description",
                "runningmachine",
                "runningcpu",
                "running-nets-host-mtu",
            }
            and not re.fullmatch(r"unused\d+", key)
        }
    )


async def observe(host, deployment_id, members):
    from app.routes.v1.proxmox._helpers import _assert_vmid_safe

    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=15) as client:
        roster = await _read(client, host, "/cluster/resources", {"type": "vm"})
        if (
            not isinstance(roster, list)
            or len(roster) > 4096
            or any(not isinstance(row, dict) for row in roster)
        ):
            raise blocked("Guest inventory is incomplete.")
        result = []
        for member in members:
            vmid = member["vm_id"]
            _assert_vmid_safe(host, vmid, "snapshot set")
            rows = [row for row in roster if row.get("vmid") == vmid]
            if (
                len(rows) != 1
                or rows[0].get("node") != host.node_name
                or rows[0].get("type") != "qemu"
                or rows[0].get("template")
            ):
                raise blocked(
                    "Every snapshot member must be an existing QEMU guest on the selected node.",
                    "SNAPSHOT_OWNERSHIP_CHANGED",
                )
            config = await _read(
                client, host, _base(host, vmid) + "/config", {"current": 1}
            )
            pending = await _read(client, host, _base(host, vmid) + "/pending")
            status = await _read(client, host, _base(host, vmid) + "/status/current")
            if (
                not isinstance(config, dict)
                or config.get("template")
                or config.get("lock")
                or f"range42-deployment:{deployment_id}"
                not in str(config.get("description", "")).splitlines()
                or config.get("name") != rows[0].get("name")
                or (member.get("vm_name") and member["vm_name"] != config.get("name"))
                or not isinstance(pending, list)
                or any(
                    not isinstance(row, dict) or "pending" in row or row.get("delete")
                    for row in pending
                )
                or not isinstance(status, dict)
                or status.get("status") not in {"running", "stopped"}
            ):
                raise blocked(
                    "A guest has pending configuration, a lock, or changed deployment ownership.",
                    "SNAPSHOT_OWNERSHIP_CHANGED",
                )
            try:
                ids = [
                    value[5:]
                    for value in config.get("smbios1", "").split(",")
                    if value.startswith("uuid=")
                ]
                if len(ids) != 1:
                    raise ValueError()
                identity = str(uuid.UUID(ids[0]))
            except (ValueError, AttributeError):
                raise blocked(
                    "Snapshot members require a stable QEMU hardware UUID.",
                    "SNAPSHOT_IDENTITY_UNAVAILABLE",
                ) from None
            result.append(
                {
                    "vm_id": vmid,
                    "vm_name": config["name"],
                    "uuid": identity,
                    "config_digest": digest(
                        {k: v for k, v in config.items() if k != "digest"}
                    ),
                    "restore_digest": _config_digest(config),
                    "status": status["status"],
                }
            )
        return result


async def _snapshot(client, host, snapshot, vmid):
    rows = await _read(client, host, _base(host, vmid) + "/snapshot")
    if (
        not isinstance(rows, list)
        or len(rows) > 4096
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise blocked("Snapshot inventory is incomplete.")
    matches = [row for row in rows if row.get("name") == snapshot.native_name]
    if len(matches) > 1:
        raise blocked("Snapshot identity is ambiguous.", "SNAPSHOT_OWNERSHIP_CHANGED")
    if not matches:
        return None
    if matches[0].get("description") != f"range42-snapshot-set:{snapshot.id}":
        raise blocked(
            "The native snapshot does not carry this set's exact marker.",
            "SNAPSHOT_OWNERSHIP_CHANGED",
        )
    config = await _read(
        client, host, _base(host, vmid) + f"/snapshot/{snapshot.native_name}/config"
    )
    if (
        not isinstance(config, dict)
        or config.get("snapstate")
        or config.get("vmstate")
        or type(matches[0].get("snaptime")) is not int
        or matches[0]["snaptime"] <= 0
    ):
        raise blocked("Snapshot configuration is unavailable or still changing.")
    return {
        "snaptime": matches[0].get("snaptime"),
        "config_digest": _config_digest(config),
    }


def _operation(snapshot, kind, members):
    operation_id = uuid.uuid4().hex
    created = now()
    plan = {
        "id": operation_id,
        "set_id": snapshot.id,
        "kind": kind,
        "target_digest": snapshot.target_digest,
        "project_sha": snapshot.project_sha,
        "members": members,
        "vmstate": False,
    }
    return SnapshotOperation(
        id=operation_id,
        set_id=snapshot.id,
        kind=kind,
        state="planned",
        plan=plan,
        plan_digest=digest(plan),
        members=[{"vm_id": row["vm_id"], "state": "planned"} for row in members],
        created_at=created,
        expires_at=created + timedelta(minutes=5),
    )


async def plan_create(deployment_id, payload):
    async with db.get_session_factory()() as session:
        deployment, host = await _deployment(session, deployment_id)
        captured = (
            deployment.project_sha,
            deployment.target_host_id,
            target_digest(host),
        )
        with tempfile.TemporaryDirectory(prefix="range42-snapshot-plan-") as temporary:
            scenario = await prepare_project_scenario(
                session, deployment, dest=Path(temporary) / "checkout", scope="runtime"
            )
            members = manifest_assignments(scenario.playbook.parent)
        if not 1 <= len(members) <= 64:
            raise blocked(
                "Snapshot sets require between one and 64 concrete QEMU guests.",
                "SNAPSHOT_MEMBERS_UNSUPPORTED",
            )
        observed = await observe(host, deployment_id, members)
        snapshot = SnapshotSet(
            id=uuid.uuid4().hex,
            deployment_id=deployment_id,
            project_sha=captured[0],
            host_id=captured[1],
            target_digest=captured[2],
            name=payload.name,
            description=payload.description,
            native_name="r42s_" + uuid.uuid4().hex[:24],
            state="planned",
            members=observed,
            created_at=now(),
        )
        operation = _operation(snapshot, "create", observed)
        # Recheck stored binding after the checkout and HTTP observations.
        await session.rollback()
        await session.execute(text("BEGIN IMMEDIATE"))
        fresh_dep, fresh_host = await _deployment(session, deployment_id)
        _binding(snapshot, fresh_dep, fresh_host)
        session.add(snapshot)
        await session.flush()
        session.add(operation)
        await session.commit()
        return await detail(deployment_id, snapshot.id, operation.id)


async def _stored_set(session, deployment_id, set_id):
    snapshot = await session.get(SnapshotSet, set_id)
    if snapshot is None or snapshot.deployment_id != deployment_id:
        raise blocked("Snapshot set not found.", "NOT_FOUND", 404)
    return snapshot


async def plan_existing(deployment_id, set_id, kind, retention=None):
    async with db.get_session_factory()() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        snapshot = await _stored_set(session, deployment_id, set_id)
        if snapshot.state not in (
            {"complete"} if kind == "rollback" else {"complete", "partial", "failed"}
        ):
            raise blocked(
                "Only terminal owned snapshot sets can be reviewed for this operation."
            )
        deployment, host = await _deployment(session, deployment_id)
        _binding(snapshot, deployment, host)
        if retention is not None:
            await _check_retention(session, snapshot, retention)
        observed = await observe(host, deployment_id, snapshot.members)
        if any(
            actual["uuid"] != original["uuid"]
            for actual, original in zip(observed, snapshot.members, strict=True)
        ):
            raise blocked(
                "A snapshot member was replaced.", "SNAPSHOT_OWNERSHIP_CHANGED"
            )
        proofs = {}
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=15) as client:
            for member in snapshot.members:
                proof = await _snapshot(client, host, snapshot, member["vm_id"])
                if kind == "rollback" and (
                    proof is None or proof != member.get("snapshot")
                ):
                    raise blocked(
                        "The completed snapshot has changed or is unavailable."
                    )
                if proof is not None:
                    proofs[str(member["vm_id"])] = proof
        if not proofs:
            raise blocked("No owned snapshots remain to operate on.")
        observed = [member for member in observed if str(member["vm_id"]) in proofs]
        operation = _operation(snapshot, kind, observed)
        operation.plan = {**operation.plan, "snapshots": proofs, "retention": retention}
        operation.plan_digest = digest(operation.plan)
        session.add(operation)
        await session.commit()
        return await detail(deployment_id, set_id, operation.id)


async def cancel_plan(deployment_id, set_id, operation_id):
    async with db.get_session_factory()() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        await _stored_set(session, deployment_id, set_id)
        operation = await session.get(SnapshotOperation, operation_id)
        if operation is None or operation.set_id != set_id:
            raise blocked("Snapshot plan not found.", "NOT_FOUND", 404)
        if operation.state != "planned" or operation.attempt_id:
            raise blocked(
                "An attempted native operation cannot be cancelled or forgotten; reconcile it read-only.",
                "SNAPSHOT_RECONCILIATION_REQUIRED",
            )
        operation.state = "cancelled"
        await session.commit()


async def _guard_members(host, snapshot, operation, members):
    if await observe(host, snapshot.deployment_id, members) != members:
        raise blocked("A snapshot member changed after review; request a new plan.")
    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=15) as client:
        for member in members:
            present = await _snapshot(client, host, snapshot, member["vm_id"])
            expected = operation.plan.get("snapshots", {}).get(str(member["vm_id"]))
            if present != expected:
                raise blocked(
                    "The reviewed native snapshot identity changed.",
                    "SNAPSHOT_OWNERSHIP_CHANGED",
                )


def _public_operation(operation):
    recovery = (
        "operator_required"
        if operation.state == "needs_review"
        else "poll_saved_tasks"
        if operation.state == "running"
        else "none"
    )
    return {
        "id": operation.id,
        "kind": operation.kind,
        "state": operation.state,
        "plan_digest": operation.plan_digest,
        "expires_at": utc(operation.expires_at).isoformat(),
        "members": operation.members,
        "reviewed_members": operation.plan["members"],
        "attempt_id": operation.attempt_id,
        "retention": operation.plan.get("retention"),
        "recovery": recovery,
    }


def _retention_policy():
    from app.routes.v1.admin.retention import read_for_deletion

    return read_for_deletion()


async def _retention_candidates(session, deployment_id, policy):
    complete = list(
        (
            await session.scalars(
                select(SnapshotSet)
                .where(
                    SnapshotSet.deployment_id == deployment_id,
                    SnapshotSet.state == "complete",
                )
                .order_by(SnapshotSet.created_at.desc(), SnapshotSet.id.desc())
                .limit(1001)
            )
        ).all()
    )
    if len(complete) > 1000:
        raise blocked(
            "Snapshot history exceeds the bounded retention review; review individual sets."
        )
    cutoff = now() - timedelta(days=policy["keep_days"])
    return [
        row
        for index, row in enumerate(complete)
        if index >= policy["keep_count"] and utc(row.created_at) <= cutoff
    ]


async def _check_retention(session, snapshot, reviewed):
    if _retention_policy() != reviewed or snapshot.id not in {
        row.id
        for row in await _retention_candidates(
            session, snapshot.deployment_id, reviewed
        )
    }:
        raise blocked(
            "Retention policy or completed snapshot history changed; review deletion again."
        )


async def plan_retention(deployment_id, limit):
    policy = _retention_policy()
    async with db.get_session_factory()() as session:
        await _deployment(session, deployment_id)
        candidates = await _retention_candidates(session, deployment_id, policy)
        ids = [row.id for row in candidates[:limit]]
    reviewed = [
        await plan_existing(deployment_id, set_id, "delete", retention=policy)
        for set_id in ids
    ]
    return {
        "policy": policy,
        "automatic_enforcement": False,
        "eligible_count": len(candidates),
        "candidates": reviewed,
    }


async def list_sets(deployment_id, offset, limit):
    async with db.get_session_factory()() as session:
        await _deployment(session, deployment_id)
        predicate = SnapshotSet.deployment_id == deployment_id
        total = await session.scalar(
            select(func.count()).select_from(SnapshotSet).where(predicate)
        )
        ids = list(
            (
                await session.scalars(
                    select(SnapshotSet.id)
                    .where(predicate)
                    .order_by(SnapshotSet.created_at.desc(), SnapshotSet.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
    return {
        "items": [await detail(deployment_id, set_id) for set_id in ids],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


async def detail(deployment_id, set_id, operation_id=None):
    async with db.get_session_factory()() as session:
        snapshot = await session.get(SnapshotSet, set_id)
        if snapshot is None or snapshot.deployment_id != deployment_id:
            raise blocked("Snapshot set not found.", "NOT_FOUND", 404)
        operations = list(
            (
                await session.scalars(
                    select(SnapshotOperation)
                    .where(SnapshotOperation.set_id == set_id)
                    .order_by(SnapshotOperation.created_at, SnapshotOperation.id)
                )
            ).all()
        )
        operation = next(
            (row for row in operations if row.id == operation_id), operations[-1]
        )
        return {
            "id": snapshot.id,
            "deployment_id": snapshot.deployment_id,
            "name": snapshot.name,
            "description": snapshot.description,
            "state": snapshot.state,
            "project_sha": snapshot.project_sha,
            "host_id": snapshot.host_id,
            "target_digest": snapshot.target_digest,
            "native_name": snapshot.native_name,
            "atomic": False,
            "vmstate": False,
            "created_at": utc(snapshot.created_at).isoformat(),
            "members": snapshot.members,
            "operation": _public_operation(operation),
            "operations": [_public_operation(row) for row in operations],
        }


def _validate_task(upid, host, vmid, kind):
    worker = {
        "create": "qmsnapshot",
        "rollback": "qmrollback",
        "delete": "qmdelsnapshot",
    }[kind]
    return (
        isinstance(upid, str)
        and len(upid) <= 512
        and re.fullmatch(
            rf"UPID:{re.escape(host.node_name)}:[A-Fa-f0-9]+:[A-Fa-f0-9]+:[A-Fa-f0-9]+:{worker}:{vmid}:[^:\s]+:",
            upid,
        )
    )


async def _dispatch(session, snapshot, operation, host, vmid):
    from app.routes.v1.proxmox.snapshots import (
        create_snapshot,
        delete_snapshot,
        rollback_snapshot,
    )
    from app.schemas.v1.proxmox import SnapshotCreateIn

    if operation.kind == "create":
        return await create_snapshot(
            host.id,
            vmid,
            SnapshotCreateIn(
                snapname=snapshot.native_name,
                description=f"range42-snapshot-set:{snapshot.id}",
                vmstate=False,
            ),
            "qemu",
            session,
        )
    if operation.kind == "rollback":
        return await rollback_snapshot(
            host.id, vmid, "qemu", snapshot.native_name, session
        )
    return await delete_snapshot(host.id, vmid, "qemu", snapshot.native_name, session)


async def execute(deployment_id, set_id, plan_digest):
    operation_id = None
    with ProvisioningLock(Path(Settings().workspace_root) / ".locks"):
        async with db.get_session_factory()() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            snapshot = await session.get(SnapshotSet, set_id)
            operation = await session.scalar(
                select(SnapshotOperation).where(
                    SnapshotOperation.set_id == set_id,
                    SnapshotOperation.plan_digest == plan_digest,
                )
            )
            if (
                snapshot is None
                or snapshot.deployment_id != deployment_id
                or operation is None
            ):
                raise blocked("Review this snapshot plan before executing it.")
            if (
                operation.state != "planned"
                or utc(operation.expires_at) <= now()
                or operation.plan_digest != digest(operation.plan)
            ):
                raise blocked("The snapshot plan expired or has already been used.")
            deployment, host = await _deployment(session, deployment_id)
            _binding(snapshot, deployment, host)
            if operation.plan.get("retention") is not None:
                await _check_retention(session, snapshot, operation.plan["retention"])
            await _guard_members(host, snapshot, operation, operation.plan["members"])
            current = (
                await session.get(Attempt, deployment.current_attempt_id)
                if deployment.current_attempt_id
                else None
            )
            if current and current.state not in TERMINAL_ATTEMPT_STATES:
                raise blocked(
                    "This deployment already has an active attempt.",
                    "ATTEMPT_IN_PROGRESS",
                )
            attempt = Attempt(
                id=uuid.uuid4().hex[:16],
                deployment_id=deployment_id,
                project_sha=deployment.project_sha,
                scope="snapshot_set",
                operation={"kind": "snapshot_set", "operation_id": operation.id},
                state="deploying",
                started_at=now(),
            )
            await acquire_lock(
                session,
                deployment_id=deployment_id,
                owner="attempt-" + attempt.id,
                interval_s=30,
            )
            session.add(attempt)
            await session.flush()
            await session.execute(
                update(SnapshotOperation)
                .where(
                    SnapshotOperation.set_id == set_id,
                    SnapshotOperation.state == "planned",
                    SnapshotOperation.id != operation.id,
                )
                .values(state="superseded")
            )
            operation.attempt_id = attempt.id
            operation.state = "running"
            deployment.current_attempt_id = attempt.id
            deployment.state = "deploying"
            snapshot.state = {
                "create": "creating",
                "rollback": "rolling_back",
                "delete": "deleting",
            }[operation.kind]
            operation_id = operation.id
            await session.commit()
            for index in range(len(operation.members)):
                rows = deepcopy(operation.members)
                try:
                    await session.refresh(deployment)
                    await session.refresh(host)
                    _binding(snapshot, deployment, host)
                    if (
                        operation.plan.get("retention") is not None
                        and _retention_policy() != operation.plan["retention"]
                    ):
                        raise blocked("Retention policy changed during this operation.")
                    await _guard_members(
                        host, snapshot, operation, [operation.plan["members"][index]]
                    )
                except Range42Error:
                    for remaining in rows[index:]:
                        remaining.update(
                            state="not_started", code="SNAPSHOT_PLAN_STALE"
                        )
                    operation.members = rows
                    await session.commit()
                    break
                rows[index].update(state="dispatching", dispatched_at=now().isoformat())
                operation.members = deepcopy(rows)
                await session.commit()  # Intent must survive before the native write.
                try:
                    result = await _dispatch(
                        session, snapshot, operation, host, rows[index]["vm_id"]
                    )
                    if not _validate_task(
                        result.upid, host, rows[index]["vm_id"], operation.kind
                    ):
                        raise blocked(
                            "The native response did not identify this member's task."
                        )
                    rows[index].update(state="accepted", upid=result.upid)
                except Exception:
                    rows[index].update(
                        state="unconfirmed", code="SNAPSHOT_DISPATCH_UNCONFIRMED"
                    )
                    for remaining in rows[index + 1 :]:
                        remaining.update(state="not_started")
                    operation.state = "needs_review"
                    snapshot.state = "needs_review"
                operation.members = deepcopy(rows)
                await session.commit()
                if operation.state == "needs_review":
                    break
    return await detail(deployment_id, set_id, operation_id)


async def reconcile_operation(operation_id):
    with ProvisioningLock(Path(Settings().workspace_root) / ".locks"):
        async with db.get_session_factory()() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            operation = await session.get(SnapshotOperation, operation_id)
            if operation is None or operation.state not in ACTIVE:
                return
            snapshot = await session.get(SnapshotSet, operation.set_id)
            deployment, host = await _deployment(session, snapshot.deployment_id)
            _binding(snapshot, deployment, host)
            rows = deepcopy(operation.members)
            async with httpx.AsyncClient(verify=proxmox_verify(), timeout=15) as client:
                for row in rows:
                    if row["state"] == "planned":
                        # The provisioning lock proves no dispatcher remains.
                        # No persisted intent means no native write was issued.
                        row.update(
                            state="not_started", code="SNAPSHOT_DISPATCH_INTERRUPTED"
                        )
                    if row["state"] not in {"accepted", "dispatching"}:
                        continue
                    if not _validate_task(
                        row.get("upid"), host, row["vm_id"], operation.kind
                    ):
                        row.update(
                            state="unconfirmed", code="SNAPSHOT_DISPATCH_UNCONFIRMED"
                        )
                        continue
                    try:
                        task = await _read(
                            client,
                            host,
                            f"/nodes/{quote(host.node_name, safe='')}/tasks/{quote(row['upid'], safe='')}/status",
                        )
                        if (
                            not isinstance(task, dict)
                            or task.get("upid") != row["upid"]
                        ):
                            raise blocked("Native task identity changed.")
                        if task.get("status") == "running":
                            continue
                        if task.get("status") != "stopped" or not isinstance(
                            task.get("exitstatus"), str
                        ):
                            raise blocked("Native task completion is unavailable.")
                        if task["exitstatus"] != "OK":
                            row.update(
                                state="failed", code="SNAPSHOT_NATIVE_TASK_FAILED"
                            )
                            continue
                        expected = next(
                            member
                            for member in snapshot.members
                            if member["vm_id"] == row["vm_id"]
                        )
                        actual = (
                            await observe(host, snapshot.deployment_id, [expected])
                        )[0]
                        if actual["uuid"] != expected["uuid"]:
                            raise blocked("Guest UUID changed.")
                        present = await _snapshot(client, host, snapshot, row["vm_id"])
                        if operation.kind == "create":
                            if (
                                present is None
                                or present["config_digest"]
                                != expected["restore_digest"]
                            ):
                                raise blocked(
                                    "The snapshot does not match the reviewed member configuration."
                                )
                            row.update(snapshot=present)
                        elif operation.kind == "delete":
                            if present is not None:
                                raise blocked("The owned snapshot still exists.")
                        elif (
                            present is None
                            or present != operation.plan["snapshots"][str(row["vm_id"])]
                            or actual["restore_digest"] != present["config_digest"]
                            or actual["status"] != "stopped"
                        ):
                            raise blocked(
                                "Rollback target configuration or stopped state is not verified."
                            )
                        row.update(state="succeeded")
                        row.pop("code", None)
                    except Range42Error:
                        row.update(code="SNAPSHOT_READBACK_UNCONFIRMED")
            operation.members = rows
            states = {row["state"] for row in rows}
            if states <= {"succeeded", "failed", "not_started"}:
                operation.state = (
                    "succeeded"
                    if states == {"succeeded"}
                    else "partial"
                    if "succeeded" in states
                    else "failed"
                )
                if operation.kind == "create":
                    snapshot.state = (
                        "complete"
                        if operation.state == "succeeded"
                        else operation.state
                    )
                    snapshot.members = [
                        {
                            **member,
                            **next(
                                row for row in rows if row["vm_id"] == member["vm_id"]
                            ),
                        }
                        for member in snapshot.members
                    ]
                elif operation.kind == "delete":
                    snapshot.state = (
                        "deleted" if operation.state == "succeeded" else "partial"
                    )
                else:
                    snapshot.state = "complete"
                attempt = await session.get(Attempt, operation.attempt_id)
                attempt.state = operation.state
                attempt.rc = 0 if operation.state == "succeeded" else 1
                attempt.ended_at = now()
                attempt.operation_result = {
                    "set_id": snapshot.id,
                    "kind": operation.kind,
                    "members": rows,
                    "atomic": False,
                }
                if deployment.current_attempt_id == attempt.id:
                    deployment.state = attempt.state
                await release_lock(
                    session, deployment_id=deployment.id, owner="attempt-" + attempt.id
                )
            elif states & {"unconfirmed", "dispatching"}:
                operation.state = snapshot.state = "needs_review"
            await session.commit()


async def reconcile(deployment_id, set_id):
    current = await detail(deployment_id, set_id)
    for operation in current["operations"]:
        if operation["state"] in ACTIVE:
            await reconcile_operation(operation["id"])
    return await detail(deployment_id, set_id)
