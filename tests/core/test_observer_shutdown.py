"""Stopping observation lets an in-flight database transaction close first."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core import orphans
from app.core.deploy_trigger import start_attempt
from app.core.models import Attempt
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize("process_finished", [False, True])
async def test_shutdown_drains_inflight_heartbeat_without_cancelling_session(tmp_path, monkeypatch, adopted, process_finished):
    _, dbmod = await _boot(tmp_path, monkeypatch)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = False

    async def heartbeat(**kwargs):
        nonlocal cancelled
        entered.set()
        try:
            await release.wait()  # Represents an in-flight database operation.
            finished.set()
        except asyncio.CancelledError:
            cancelled = True
            raise

    monkeypatch.setattr("app.core.deploy_trigger.keep_attempt_lock", heartbeat)
    monkeypatch.setattr(orphans, "keep_attempt_lock", heartbeat)
    stopping = None
    try:
        if adopted:
            monkeypatch.setattr(orphans, "_watcher", lambda *args: None)
            monkeypatch.setattr(orphans, "process_matches", lambda *args: not (process_finished and entered.is_set()))
            observer = asyncio.create_task(orphans._observe(SimpleNamespace(id="deployment"),
                SimpleNamespace(id="observer"), tmp_path, 123))
            orphans.track_attempt("observer", observer)
        else:
            await seed_scenario(dbmod, tmp_path)

            class Handle:
                pid = None

                async def wait(self):
                    if process_finished:
                        await entered.wait()
                        return 0
                    await asyncio.Event().wait()

                async def kill(self):
                    raise AssertionError("API shutdown must preserve the runner")

            class Runner:
                async def start(self, **kwargs):
                    return Handle()

            async with dbmod.get_session_factory()() as session:
                attempt = Attempt(id="observer", deployment_id="dep-1", scope="full", state="pending")
                session.add(attempt)
                await session.commit()
                await start_attempt(session, attempt=attempt, runner=Runner())
        await asyncio.wait_for(entered.wait(), 5)
        if process_finished:
            await asyncio.sleep(0.3)  # The monitor is now draining its workers.
        stopping = asyncio.create_task(orphans.stop_observers())
        await asyncio.sleep(0.02)
        assert not cancelled, "Stopping observation cancelled a database operation"
        assert not stopping.done(), "Shutdown returned before the heartbeat closed"
        release.set()
        await asyncio.wait_for(stopping, 5)
        assert finished.is_set()
    finally:
        release.set()
        if stopping:
            await asyncio.gather(stopping, return_exceptions=True)
        await orphans.stop_observers()
        await dbmod.dispose_engine()
