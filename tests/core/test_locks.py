import pytest
from datetime import datetime, timezone, timedelta

from app.core.db import build_engine, session_factory
from app.core.models import (
    Base,
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
