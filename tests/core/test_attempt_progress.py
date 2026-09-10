"""Running and recovered attempts publish their canonical event cursor."""
import asyncio
import json

import pytest

from app.core.models import Attempt, Deployment, WorkspaceLock
from tests.core.test_attempt_lifecycle import lifecycle_db as lifecycle_db
from tests.routes.test_project_scenario_execution import _boot, seed_scenario
from tests.fixtures.fake_runner import FakeRunner


async def wait_for_cursor(factory, attempt_id, minimum):
    async with asyncio.timeout(2):
        while True:
            async with factory() as session:
                attempt = await session.get(Attempt, attempt_id)
                if attempt.event_cursor_tip >= minimum:
                    return attempt
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_running_attempt_reports_events_before_process_completion(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger

    finished = asyncio.Event()

    class Handle:
        pid = None

        async def wait(self):
            await finished.wait()
            return 0

        async def kill(self):
            finished.set()

    class Runner:
        async def start(self, **kwargs):
            events = kwargs["private_data_dir"] / "job_events"
            events.mkdir()
            (events / "1-progress.json").write_text(json.dumps({
                "event": "verbose", "stdout": "still deploying",
            }))
            return Handle()

    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            attempt = Attempt(id="progress", deployment_id="dep-1", scope="full", state="pending")
            session.add(attempt)
            await session.commit()
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=Runner())
        attempt = await wait_for_cursor(dbmod.get_session_factory(), "progress", 2)
        assert attempt.event_cursor_tip == 2
        assert attempt.state == "deploying"
        assert attempt.ended_at is None
        assert not finished.is_set()
    finally:
        finished.set()
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_recovered_live_attempt_reports_replayed_cursor(lifecycle_db, tmp_path, monkeypatch):
    from app.core import attempt_lifecycle, orphans

    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    running = True
    monkeypatch.setattr(orphans, "process_matches", lambda *args: running)
    artifact = tmp_path / "runner" / "att"
    events = artifact / "job_events"
    events.mkdir(parents=True)
    (artifact / "redaction.json").write_text("[]")
    (artifact / "rc").write_text("0")
    (events / "1-event.json").write_text(json.dumps({"event": "verbose", "stdout": "resumed"}))
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        attempt.state = "deploying"
        dep = await session.get(Deployment, "dep")
        await session.commit()
    observer = asyncio.create_task(orphans._observe(dep, attempt, artifact, 123))
    try:
        current = await wait_for_cursor(lifecycle_db, "att", 1)
        assert current.state == "deploying"
        assert current.ended_at is None
        assert not observer.done()
    finally:
        running = False
        await asyncio.wait_for(observer, timeout=2)


@pytest.mark.asyncio
async def test_progress_updates_are_monotonic_and_preserve_attempt_state(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle

    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    await attempt_lifecycle.advance_attempt_cursor(attempt_id="att", event_cursor_tip=12)
    await attempt_lifecycle.advance_attempt_cursor(attempt_id="att", event_cursor_tip=4)
    await attempt_lifecycle.advance_attempt_cursor(attempt_id="missing", event_cursor_tip=30)
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.event_cursor_tip == 12
        assert attempt.state == "pending"
        assert attempt.ended_at is None
        assert attempt.rc is None
        assert await session.get(WorkspaceLock, "dep") is not None
        assert await session.get(Attempt, "missing") is None

    await attempt_lifecycle.finish_attempt(attempt_id="att", rc=0, event_cursor_tip=14)
    await attempt_lifecycle.advance_attempt_cursor(attempt_id="att", event_cursor_tip=12)
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.event_cursor_tip == 14
        assert attempt.state == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_ca", [False, True])
async def test_runner_inherits_explicit_proxmox_ca_without_disabling_verification(tmp_path, monkeypatch, custom_ca):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger

    ca_file = tmp_path / "private-ca.pem"
    if custom_ca:
        monkeypatch.setenv("RANGE42_PROXMOX_CA_FILE", str(ca_file))
    else:
        monkeypatch.delenv("RANGE42_PROXMOX_CA_FILE", raising=False)

    class RecordingRunner(FakeRunner):
        async def start(self, **kwargs):
            self.arguments = kwargs
            return await super().start(**kwargs)

    runner = RecordingRunner()
    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            attempt = Attempt(id="verified-tls", deployment_id="dep-1", scope="full", state="pending")
            session.add(attempt)
            await session.commit()
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
        environment = runner.arguments["envvars"]
        for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            if custom_ca:
                assert environment[name] == str(ca_file)
            else:
                assert name not in environment
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()
