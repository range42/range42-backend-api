"""Durable runner lifecycle survives the request session closing."""
import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.db import build_engine, session_factory
from app.core.models import Attempt, Base, Deployment, Project, ProxmoxHost, Source, WorkspaceLock


@pytest_asyncio.fixture
async def lifecycle_db(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    factory = session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(Source(id="s", provider="github", base_url="https://github.com", auth_kind="none"))
        session.add(ProxmoxHost(id="h", name="pve", api_url="https://pve:8006", node_name="pve", token_ref="t"))
        await session.commit()
        session.add(Project(id="p", name="p", source_id="s", branch_strategy="shared_repo_subdir"))
        await session.commit()
        session.add(Deployment(id="dep", codename="ALPHA", scenario_label="demo", project_id="p",
                               target_host_id="h", current_attempt_id="att", workspace_path=str(tmp_path)))
        await session.commit()
        session.add(Attempt(id="att", deployment_id="dep", scope="full", state="pending"))
        session.add(WorkspaceLock(deployment_id="dep", owner="attempt-att", heartbeat_interval_s=30))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runner_start_persists_process_and_active_states(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    before = datetime.now(timezone.utc)
    assert await attempt_lifecycle.mark_attempt_running(
        attempt_id="att", pid=1234, artifact_dir=tmp_path / "runner" / "att",
    ) is True
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.state == "deploying"
        assert attempt.pid == 1234
        assert attempt.artifact_dir == str(tmp_path / "runner" / "att")
        assert attempt.started_at.replace(tzinfo=timezone.utc) >= before
        assert attempt.ended_at is None
        assert (await session.get(Deployment, "dep")).state == "deploying"
        assert await session.get(WorkspaceLock, "dep") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("rc", "expected"), [(0, "succeeded"), (2, "failed"), (-15, "failed")])
async def test_process_exit_persists_terminal_state_and_releases_lock(lifecycle_db, monkeypatch, rc, expected):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    assert await attempt_lifecycle.finish_attempt(attempt_id="att", rc=rc, event_cursor_tip=8) == expected
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.state == expected
        assert attempt.rc == rc
        assert attempt.ended_at is not None
        assert attempt.event_cursor_tip == 8
        assert (await session.get(Deployment, "dep")).state == expected
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_monitoring_error_fails_attempt_without_inventing_process_exit_code(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    state = await attempt_lifecycle.finish_attempt(attempt_id="att", rc=None, error_code="RUNNER_MONITOR_FAILED")
    assert state == "failed"
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.rc is None
        assert attempt.sub_reason == "RUNNER_MONITOR_FAILED"
        assert attempt.ended_at is not None
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rc", [0, -15])
async def test_process_exit_preserves_requested_cancellation(lifecycle_db, monkeypatch, rc):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    cancelled_at = datetime(2026, 9, 10, 8, 0)
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        attempt.state = "cancelled"
        attempt.ended_at = cancelled_at
        await session.commit()
    assert await attempt_lifecycle.finish_attempt(attempt_id="att", rc=rc) == "cancelled"
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.state == "cancelled"
        assert attempt.ended_at == cancelled_at
        assert attempt.rc == rc
        assert (await session.get(Deployment, "dep")).state == "cancelled"
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_cancelled_background_task_is_terminal(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    assert await attempt_lifecycle.finish_attempt(attempt_id="att", rc=None, cancelled=True) == "cancelled"
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).ended_at is not None
        assert (await session.get(Deployment, "dep")).state == "cancelled"
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_old_attempt_never_overwrites_new_attempt_or_releases_its_lock(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    async with lifecycle_db() as session:
        dep = await session.get(Deployment, "dep")
        dep.current_attempt_id = "new"
        dep.state = "pending"
        (await session.get(WorkspaceLock, "dep")).owner = "attempt-new"
        await session.commit()
    await attempt_lifecycle.mark_attempt_running(attempt_id="att", pid=1234, artifact_dir=tmp_path)
    async with lifecycle_db() as session:
        assert (await session.get(Deployment, "dep")).state == "pending"
    await attempt_lifecycle.finish_attempt(attempt_id="att", rc=0)
    async with lifecycle_db() as session:
        assert (await session.get(Deployment, "dep")).state == "pending"
        assert (await session.get(WorkspaceLock, "dep")).owner == "attempt-new"


@pytest.mark.asyncio
async def test_late_start_cannot_resurrect_terminal_attempt(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    await attempt_lifecycle.finish_attempt(attempt_id="att", rc=None, cancelled=True)
    assert await attempt_lifecycle.mark_attempt_running(attempt_id="att", pid=1234, artifact_dir=tmp_path) is False
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).state == "cancelled"
        assert (await session.get(Deployment, "dep")).state == "cancelled"


@pytest.mark.asyncio
async def test_repeated_completion_preserves_result_and_advances_event_cursor(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    await attempt_lifecycle.finish_attempt(attempt_id="att", rc=0, event_cursor_tip=10)
    async with lifecycle_db() as session:
        ended_at = (await session.get(Attempt, "att")).ended_at
    assert await attempt_lifecycle.finish_attempt(attempt_id="att", rc=1, event_cursor_tip=4) == "succeeded"
    async with lifecycle_db() as session:
        attempt = (await session.execute(select(Attempt))).scalar_one()
        assert attempt.rc == 0
        assert attempt.event_cursor_tip == 10
        assert attempt.ended_at == ended_at


@pytest.mark.asyncio
async def test_missing_attempt_is_not_recreated(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    assert await attempt_lifecycle.mark_attempt_running(attempt_id="missing", pid=1, artifact_dir=tmp_path) is False
    assert await attempt_lifecycle.finish_attempt(attempt_id="missing", rc=0) is None


@pytest.mark.asyncio
async def test_unobserved_exit_is_unknown_instead_of_inventing_failure(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    assert await attempt_lifecycle.finish_attempt(
        attempt_id="att", rc=None, unknown=True, error_code="RUNNER_EXIT_UNOBSERVED",
    ) == "unknown"
    async with lifecycle_db() as session:
        assert (await session.get(Deployment, "dep")).state == "unknown"
        assert (await session.get(Attempt, "att")).rc is None
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("rc", "expected"), [("0", "succeeded"), ("2", "failed"), (None, "unknown")])
async def test_restart_reconciles_per_attempt_result(lifecycle_db, tmp_path, monkeypatch, rc, expected):
    from app.core import attempt_lifecycle, orphans
    monkeypatch.setattr(orphans, "get_session_factory", lambda: lifecycle_db, raising=False)
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    from dataclasses import replace
    monkeypatch.setattr(orphans, "settings", replace(orphans.settings, workspace_root=tmp_path))
    artifact = tmp_path / "runner" / "att"
    artifact.mkdir(parents=True)
    (artifact / "pid").write_text("99999999")
    if rc is not None:
        (artifact / "rc").write_text(rc)
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        attempt.state = "deploying"
        attempt.pid = 99999999
        attempt.artifact_dir = str(artifact)
        await session.commit()
    await orphans.reconcile_once()
    # A repeated reconciliation is idempotent, including its final event.
    await orphans.reconcile_once()
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).state == expected
        assert (await session.get(Deployment, "dep")).state == expected
        assert await session.get(WorkspaceLock, "dep") is None
    from app.core.events import EventsReader
    events = list(EventsReader(tmp_path / "events.jsonl").read_range())
    assert len([event for event in events if event["event_type"] == "attempt_end"]) == 1


@pytest.mark.asyncio
async def test_restart_adopts_live_process_and_redacts_replayed_output(lifecycle_db, tmp_path, monkeypatch):
    import json
    import sys
    from app.core import attempt_lifecycle, orphans
    from app.core.events import EventsReader
    from app.core.runner_detached import record_process_identity
    monkeypatch.setattr(orphans, "get_session_factory", lambda: lifecycle_db)
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    artifact = tmp_path / "runner" / "att"
    events_dir = artifact / "job_events"
    events_dir.mkdir(parents=True)
    (artifact / "redaction.json").write_text(json.dumps(["recovered-secret"]))
    (events_dir / "1-event.json").write_text(json.dumps({"event": "verbose", "stdout": "value=recovered-secret"}))
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    try:
        record_process_identity(artifact, proc.pid)
        async with lifecycle_db() as session:
            attempt = await session.get(Attempt, "att")
            attempt.state = "deploying"
            attempt.pid = proc.pid
            attempt.artifact_dir = str(artifact)
            await session.commit()
        await orphans.reconcile_once()
        assert "att" in orphans._TASKS
        task = orphans._TASKS["att"]
        await orphans.reconcile_once()
        assert orphans._TASKS["att"] is task
        (artifact / "rc").write_text("0")
        proc.terminate()
        await proc.wait()
        await asyncio.wait_for(task, timeout=3)
        async with lifecycle_db() as session:
            assert (await session.get(Attempt, "att")).state == "succeeded"
        saved = (tmp_path / "events.jsonl").read_text()
        assert "recovered-secret" not in saved
        assert len(list(EventsReader(tmp_path / "events.jsonl").read_range())) == 2
        assert not (artifact / "redaction.json").exists()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await orphans.stop_observers()


@pytest.mark.asyncio
async def test_restart_reconciles_setup_interrupted_before_spawn(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle, orphans
    monkeypatch.setattr(orphans, "get_session_factory", lambda: lifecycle_db)
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    async with lifecycle_db() as session:
        (await session.get(Attempt, "att")).started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await session.commit()
    await orphans.reconcile_once()
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).state == "unknown"
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_restart_finishes_cancelled_attempt_still_holding_lock(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle, orphans
    monkeypatch.setattr(orphans, "get_session_factory", lambda: lifecycle_db)
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    artifact = tmp_path / "runner" / "att"
    artifact.mkdir(parents=True)
    (artifact / "rc").write_text("254")
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        attempt.state, attempt.pid, attempt.artifact_dir = "cancelled", 99999999, str(artifact)
        await session.commit()
    await orphans.reconcile_once()
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).rc == 254
        assert (await session.get(Deployment, "dep")).state == "cancelled"
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_heartbeat_renews_only_the_owned_attempt_lock(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    before = datetime.now(timezone.utc)
    assert await attempt_lifecycle.heartbeat_attempt(attempt_id="att", deployment_id="dep") is True
    async with lifecycle_db() as session:
        renewed = (await session.get(WorkspaceLock, "dep")).heartbeat_at
        assert renewed.replace(tzinfo=timezone.utc) >= before
    assert await attempt_lifecycle.heartbeat_attempt(attempt_id="other", deployment_id="dep") is False
    async with lifecycle_db() as session:
        assert (await session.get(WorkspaceLock, "dep")).heartbeat_at == renewed


@pytest.mark.asyncio
async def test_background_heartbeat_stops_when_signalled(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    before = datetime.now(timezone.utc)
    stop = asyncio.Event()
    task = asyncio.create_task(attempt_lifecycle.keep_attempt_lock(
        attempt_id="att", deployment_id="dep", stop=stop, interval_s=0.01,
    ))
    try:
        async with asyncio.timeout(2):
            while True:
                async with lifecycle_db() as session:
                    ts = (await session.get(WorkspaceLock, "dep")).heartbeat_at
                if ts.replace(tzinfo=timezone.utc) >= before:
                    break
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=1)
    assert task.exception() is None


@pytest.mark.asyncio
async def test_background_heartbeat_ends_if_lock_ownership_is_lost(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    await asyncio.wait_for(attempt_lifecycle.keep_attempt_lock(
        attempt_id="other", deployment_id="dep", stop=asyncio.Event(), interval_s=30,
    ), timeout=1)
