"""A retained run pause must fence every managed VM entry point, across aliases."""

import asyncio
from datetime import datetime, timezone
from importlib.util import find_spec

import pytest
from sqlalchemy import text

from app.core.db import build_engine, session_factory
from app.core.errors import Range42Error
from app.core.models import (
    Base,
    Deployment,
    Project,
    ProxmoxHost,
    Source,
    Attempt,
    WorkspaceLock,
)
from app.core.allocation_models import DeploymentAllocation


def service():
    assert find_spec("app.core.deployment_admission"), (
        "Missing durable per-deployment write admission"
    )
    from app.core import deployment_admission

    return deployment_admission


@pytest.fixture
async def env(tmp_path, monkeypatch):
    mod = service()
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/admission.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    from app.core import db

    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_Session", factory)
    async with factory() as session:
        session.add(
            Source(
                id="s",
                provider="github",
                base_url="https://github.com",
                auth_kind="none",
            )
        )
        session.add(
            ProxmoxHost(
                id="h",
                name="h",
                api_url="https://pve.invalid",
                node_name="pve",
                token_ref="synthetic@pve!key=PRIVATE",
            )
        )
        await session.flush()
        session.add(
            Project(
                id="p", name="p", source_id="s", branch_strategy="shared_repo_subdir"
            )
        )
        await session.flush()
        for name, vmid in [("run", 45000), ("other", 45002)]:
            session.add(
                Deployment(
                    id=name,
                    codename=name.upper(),
                    scenario_label="lab",
                    project_id="p",
                    target_host_id="h",
                    project_sha="a" * 40,
                    workspace_path=str(tmp_path / name),
                    state="succeeded",
                )
            )
            await session.flush()
            session.add(
                DeploymentAllocation(
                    id=name,
                    deployment_id=name,
                    project_sha="a" * 40,
                    host_id="h",
                    api_url="https://pve.invalid",
                    node_name="pve",
                    assignments=[{"vm_id": vmid, "nics": []}],
                    created_at=datetime.now(timezone.utc),
                )
            )
        await session.commit()
    yield mod, factory, engine, tmp_path
    await engine.dispose()


async def change(env, action="pause", **kwargs):
    mod, factory, _, _ = env
    async with factory() as session:
        return await mod.change(
            session,
            "run",
            action=action,
            expected_revision=kwargs.pop("expected_revision", 0),
            request_id=kwargs.pop("request_id", "1" * 32),
            actor=kwargs.pop("actor", "director"),
            **kwargs,
        )


@pytest.mark.asyncio
async def test_pause_is_retained_and_lost_reply_does_not_create_another_pause(env):
    mod, factory, engine, _ = env
    first = await change(env)
    assert first["state"] == "closed" and first["scope"] == "managed_deployment_writers"
    assert first["vm_ids"] == [45000] and first["revision"] == 1
    assert (
        first["remote_quiescence_verified"] is False
        and first["capacity_borrowing_allowed"] is False
    )
    await engine.dispose()
    recovered = await change(env, actor="another-admin")
    assert recovered["pause_id"] == first["pause_id"]
    assert recovered["changed_by"] == "director" and recovered["revision"] == 1
    async with factory() as session:
        with pytest.raises(Range42Error, match="paused") as error:
            await mod.assert_vm_admitted(session, 45000)
        assert error.value.code == "DEPLOYMENT_WRITES_PAUSED"
        await mod.assert_vm_admitted(session, 45002)
        await mod.assert_deployment_admitted(session, "other")


@pytest.mark.asyncio
async def test_exact_pause_and_revision_required_to_resume_and_no_automatic_resume(env):
    first = await change(env)
    for overrides in [
        {"pause_id": "f" * 32},
        {"expected_revision": 0},
        {"request_id": "1" * 32},
    ]:
        request = {
            "expected_revision": 1,
            "pause_id": first["pause_id"],
            "request_id": "2" * 32,
            **overrides,
        }
        with pytest.raises(Range42Error) as error:
            await change(env, "resume", **request)
        assert error.value.code == "DEPLOYMENT_ADMISSION_CHANGED"
    resumed = await change(
        env,
        "resume",
        expected_revision=1,
        pause_id=first["pause_id"],
        request_id="2" * 32,
    )
    assert resumed["state"] == "open" and resumed["revision"] == 2
    assert (
        await change(
            env,
            "resume",
            expected_revision=1,
            pause_id=first["pause_id"],
            request_id="2" * 32,
        )
    )["revision"] == 2
    second = await change(env, expected_revision=2, request_id="3" * 32)
    assert second["pause_id"] != first["pause_id"]
    with pytest.raises(Range42Error):
        await change(
            env,
            "resume",
            expected_revision=1,
            pause_id=first["pause_id"],
            request_id="2" * 32,
        )


@pytest.mark.asyncio
async def test_parallel_administrators_cannot_overwrite_pause(env):
    async def submit(request_id):
        try:
            return await change(env, request_id=request_id)
        except Range42Error as error:
            return error.code

    results = await asyncio.gather(submit("1" * 32), submit("2" * 32))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert any(
        value in ("DEPLOYMENT_ADMISSION_CHANGED", "PROVISIONING_BUSY")
        for value in results
        if isinstance(value, str)
    )


@pytest.mark.asyncio
async def test_every_old_attempt_and_lock_counts_toward_draining(env):
    mod, factory, _, _ = env
    async with factory() as session:
        session.add(
            Attempt(
                id="old-pending", deployment_id="run", scope="full", state="pending"
            )
        )
        session.add(
            Attempt(
                id="old-running", deployment_id="run", scope="full", state="deploying"
            )
        )
        session.add(WorkspaceLock(deployment_id="run", owner="old-runner"))
        await session.commit()
    paused = await change(env)
    assert (
        paused["state"] == "draining"
        and paused["active_attempts"] == 2
        and paused["held_workspace_locks"] == 1
    )
    with pytest.raises(Range42Error) as error:
        await change(
            env,
            "resume",
            expected_revision=1,
            pause_id=paused["pause_id"],
            request_id="2" * 32,
        )
    assert error.value.code == "DEPLOYMENT_ADMISSION_DRAINING"
    async with factory() as session:
        await session.execute(text("UPDATE attempts SET state='succeeded'"))
        await session.execute(text("DELETE FROM workspace_locks"))
        await session.commit()
    async with factory() as session:
        state = await mod.snapshot(session, "run")
        assert state["state"] == "closed" and state["revision"] == 1
        assert state["remote_quiescence_verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["assignments", "binding", "missing", "paused-state"]
)
async def test_changed_or_corrupt_owned_scope_fails_closed(env, damage):
    mod, factory, _, _ = env
    await change(env)
    async with factory() as session:
        if damage == "assignments":
            (await session.get(DeploymentAllocation, "run")).assignments = [
                {"vm_id": 45004, "nics": []}
            ]
        elif damage == "binding":
            (await session.get(ProxmoxHost, "h")).api_url = "https://changed.invalid"
        elif damage == "missing":
            await session.delete(await session.get(DeploymentAllocation, "run"))
        else:
            await session.execute(
                text("UPDATE deployment_admissions SET state='damaged'")
            )
        await session.commit()
    async with factory() as session:
        for vmid in [45000, 45004, 45002]:
            with pytest.raises(Range42Error) as error:
                await mod.assert_vm_admitted(session, vmid)
            assert error.value.code == "DEPLOYMENT_ADMISSION_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_pause_requires_durable_backend_owned_assignments(env):
    _, factory, _, _ = env
    async with factory() as session:
        await session.delete(await session.get(DeploymentAllocation, "run"))
        await session.commit()
    with pytest.raises(Range42Error) as error:
        await change(env)
    assert error.value.code == "DEPLOYMENT_ADMISSION_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_existing_provisioner_lock_prevents_a_false_closed_response(
    env, monkeypatch
):
    _, _, _, root = env
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(root))
    from app.core.locks import ProvisioningLock

    with ProvisioningLock(root / ".locks"):
        with pytest.raises(Range42Error) as error:
            await change(env)
    assert error.value.code == "PROVISIONING_BUSY"


@pytest.mark.asyncio
async def test_pending_attempt_is_refused_before_checkout_or_runner(env, monkeypatch):
    mod, factory, _, root = env
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(root))
    await change(env)
    async with factory() as session:
        session.add(
            Attempt(
                id="pending-before-pause",
                deployment_id="run",
                scope="full",
                state="pending",
            )
        )
        await session.commit()
    from app.core import deploy_trigger

    called = []

    async def no_spawn(*args, **kwargs):
        called.append("spawn")

    monkeypatch.setattr(deploy_trigger, "_start_attempt", no_spawn)
    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await deploy_trigger.start_attempt(
                session, attempt=await session.get(Attempt, "pending-before-pause")
            )
    assert error.value.code == "DEPLOYMENT_WRITES_PAUSED" and not called


def test_managed_writer_admission_exists():
    service()


@pytest.mark.asyncio
async def test_unresolved_remote_snapshot_remains_draining_even_after_lock_loss(env):
    _, factory, _, _ = env
    from app.core.snapshot_models import SnapshotSet, SnapshotOperation

    instant = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(
            SnapshotSet(
                id="snapshot",
                deployment_id="run",
                project_sha="a" * 40,
                host_id="h",
                target_digest="b" * 64,
                name="before",
                description="",
                native_name="fixture-snapshot",
                state="needs_review",
                members=[],
                created_at=instant,
            )
        )
        await session.flush()
        session.add(
            SnapshotOperation(
                id="operation",
                set_id="snapshot",
                kind="create",
                state="needs_review",
                plan_digest="c" * 64,
                plan={},
                members=[],
                created_at=instant,
                expires_at=instant,
            )
        )
        await session.commit()
    paused = await change(env)
    assert paused["state"] == "draining" and paused["active_snapshot_operations"] == 1
    with pytest.raises(Range42Error) as error:
        await change(
            env,
            "resume",
            expected_revision=1,
            pause_id=paused["pause_id"],
            request_id="2" * 32,
        )
    assert error.value.code == "DEPLOYMENT_ADMISSION_DRAINING"
