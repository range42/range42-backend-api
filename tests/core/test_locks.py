import asyncio
import json
import sys

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from app.core.db import build_engine, session_factory
from app.core.models import (
    Base,
    Attempt,
    Deployment,
    ProxmoxHost,
    Project,
    Source,
    WorkspaceLock,
)
from app.core.locks import (
    acquire_lock,
    release_lock,
    heartbeat,
    cleanup_stale_locks,
    LockHeldError,
)
from app.core.runner_detached import record_process_identity


async def _seed(session):
    session.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
    session.add(
        ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t")
    )
    await session.flush()
    session.add(
        Project(id="p", name="p", source_id="s", branch_strategy="shared_repo_subdir")
    )
    await session.flush()
    session.add(
        Deployment(
            id="dep-1",
            codename="A",
            scenario_label="b",
            project_id="p",
            target_host_id="h",
            team_count=1,
            state="pending",
            workspace_path="/tmp/A-b",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_acquire_rejects_double_hold(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        await _seed(s)
    async with Session() as s:
        await acquire_lock(
            s, deployment_id="dep-1", owner="worker-1", interval_s=30
        )
        await s.commit()
    async with Session() as s:
        with pytest.raises(LockHeldError):
            await acquire_lock(
                s, deployment_id="dep-1", owner="worker-2", interval_s=30
            )


@pytest.mark.asyncio
async def test_stale_lock_cleanup(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        await _seed(s)
        stale = WorkspaceLock(
            deployment_id="dep-1", owner="dead", heartbeat_interval_s=10
        )
        stale.acquired_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        stale.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        s.add(stale)
        await s.commit()
    async with Session() as s:
        cleaned = await cleanup_stale_locks(s)
        await s.commit()
        assert cleaned == 1
    async with Session() as s:
        await acquire_lock(
            s, deployment_id="dep-1", owner="fresh", interval_s=30
        )
        await s.commit()


@pytest.mark.asyncio
async def test_acquire_evicts_stale_predecessor(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        await _seed(s)
        stale = WorkspaceLock(
            deployment_id="dep-1", owner="dead", heartbeat_interval_s=10
        )
        stale.acquired_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        stale.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        s.add(stale)
        await s.commit()
    async with Session() as s:
        await acquire_lock(
            s, deployment_id="dep-1", owner="fresh", interval_s=30
        )
        await s.commit()


@pytest.mark.asyncio
async def test_release_only_by_owner(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        await _seed(s)
    async with Session() as s:
        await acquire_lock(
            s, deployment_id="dep-1", owner="worker-1", interval_s=30
        )
        await s.commit()
    async with Session() as s:
        assert await release_lock(s, deployment_id="dep-1", owner="intruder") is False
        await s.commit()
    async with Session() as s:
        assert await release_lock(s, deployment_id="dep-1", owner="worker-1") is True
        await s.commit()


@pytest.mark.asyncio
async def test_heartbeat_updates_timestamp(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        await _seed(s)
    async with Session() as s:
        lock = await acquire_lock(
            s, deployment_id="dep-1", owner="worker-1", interval_s=30
        )
        lock.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=20)
        await s.commit()
    async with Session() as s:
        assert await heartbeat(s, deployment_id="dep-1", owner="worker-1") is True
        await s.commit()
    async with Session() as s:
        # Wrong owner rejected
        assert await heartbeat(s, deployment_id="dep-1", owner="x") is False


@pytest_asyncio.fixture
async def stale_runner_lock(tmp_path):
    """A cancelled attempt can still own a live process after API shutdown."""
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = session_factory(engine)
    workspace = tmp_path / "workspace"
    artifact = workspace / "runner" / "old-run"
    artifact.mkdir(parents=True)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    try:
        assert record_process_identity(artifact, process.pid)
        (artifact / "pid").write_text(str(process.pid))
        async with sessions() as session:
            await _seed(session)
            deployment = await session.get(Deployment, "dep-1")
            deployment.workspace_path = str(workspace)
            session.add(Attempt(
                id="old-run", deployment_id="dep-1", scope="configure",
                state="cancelled", pid=process.pid, artifact_dir=str(artifact),
            ))
            session.add(WorkspaceLock(
                deployment_id="dep-1", owner="attempt-old-run", heartbeat_interval_s=10,
                heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            ))
            await session.commit()
        yield sessions, process, artifact
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted_pid", [True, False])
@pytest.mark.parametrize("entrypoint", ["cleanup", "acquire"])
async def test_expired_heartbeat_cannot_replace_verified_live_runner(stale_runner_lock, persisted_pid, entrypoint):
    sessions, process, _ = stale_runner_lock
    async with sessions() as session:
        if not persisted_pid:
            # A crash can occur after spawn artifacts but before DB PID update.
            attempt = await session.get(Attempt, "old-run")
            attempt.pid = None
            await session.commit()
        if entrypoint == "cleanup":
            assert await cleanup_stale_locks(session, deployment_id="dep-1") == 0
        else:
            with pytest.raises(LockHeldError):
                await acquire_lock(session, deployment_id="dep-1", owner="replacement", interval_s=30)
        await session.commit()
    process.terminate()
    await process.wait()
    async with sessions() as session:
        assert await cleanup_stale_locks(session, deployment_id="dep-1") == 1
        await session.commit()
        await acquire_lock(session, deployment_id="dep-1", owner="replacement", interval_s=30)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["start_time", "boot_id", "missing_identity", "wrong_artifact", "outside_workspace", "wrong_deployment"])
async def test_unverified_process_cannot_pin_stale_lock(stale_runner_lock, invalid, tmp_path):
    sessions, _, artifact = stale_runner_lock
    async with sessions() as session:
        attempt = await session.get(Attempt, "old-run")
        if invalid in {"start_time", "boot_id"}:
            identity = json.loads((artifact / "process.json").read_text())
            identity[invalid] = "different-process"
            (artifact / "process.json").write_text(json.dumps(identity))
        elif invalid == "missing_identity":
            (artifact / "process.json").unlink()
        elif invalid == "wrong_artifact":
            attempt.artifact_dir = str(tmp_path / "unrelated")
        elif invalid == "outside_workspace":
            outside = tmp_path / "other-runner"
            artifact.parent.rename(outside)
            artifact.parent.symlink_to(outside, target_is_directory=True)
        else:
            session.add(Deployment(
                id="dep-2", codename="other", scenario_label="b", project_id="p",
                target_host_id="h", workspace_path=str(artifact.parent.parent),
            ))
            await session.flush()
            attempt.deployment_id = "dep-2"
        await session.commit()
        await acquire_lock(session, deployment_id="dep-1", owner="replacement", interval_s=30)
        await session.commit()
        assert (await session.get(WorkspaceLock, "dep-1")).owner == "replacement"
