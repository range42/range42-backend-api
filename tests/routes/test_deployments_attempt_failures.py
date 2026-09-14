"""Setup failures remain visible as terminal attempts and release owned locks."""
import asyncio
from datetime import datetime, timedelta, timezone
from importlib import reload

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.events import EventsReader, EventsWriter
from app.core.locks import acquire_lock
from app.core.models import Attempt, Base, Deployment, Project, ProxmoxHost, Source, WorkspaceLock


@pytest_asyncio.fixture
async def attempt_api(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    from app.core import config as cfg
    import app.core.db as dbmod
    reload(cfg)
    reload(dbmod)
    async with dbmod.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ws = tmp_path / "ALPHA-demo"
    ws.mkdir()
    async with dbmod.get_session_factory()() as session:
        session.add(Source(id="s", provider="github", base_url="https://github.com",
                           auth_kind="none"))
        session.add(ProxmoxHost(id="h", name="pve", api_url="https://pve:8006",
                                node_name="pve", token_ref="test"))
        await session.commit()
        session.add(Project(id="p", name="project", source_id="s",
                            branch_strategy="shared_repo_subdir"))
        await session.commit()
        session.add(Deployment(id="dep", codename="ALPHA", scenario_label="demo",
                               project_id="p", target_host_id="h", workspace_path=str(ws)))
        await session.commit()
    from app.main import create_app
    try:
        async with AsyncClient(transport=ASGITransport(app=create_app()),
                               base_url="http://t", follow_redirects=True) as client:
            yield client, dbmod, ws
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["runtime", "checkout", "database"])
async def test_setup_failure_is_terminal_and_releases_owned_lock(attempt_api, monkeypatch, failure):
    client, dbmod, ws = attempt_api

    async def fail_setup(session, *, attempt):
        await acquire_lock(session, deployment_id="dep", owner=f"attempt-{attempt.id}", interval_s=30)
        await session.commit()
        EventsWriter(ws / "events.jsonl").append(
            {"event_type": "attempt_start", "payload": {}},
            attempt_id=attempt.id, deployment_id="dep",
        )
        if failure == "checkout":
            from app.core.errors import ProjectCheckoutError
            raise ProjectCheckoutError(message="checkout failed with secret-token")
        if failure == "database":
            # Fail a real transaction, as setup can do before raising.
            session.add(Source(id="s", provider="github", base_url="https://github.com",
                               auth_kind="none"))
            await session.flush()
        raise RuntimeError("runner setup failed with secret-token")

    monkeypatch.setattr("app.core.deploy_trigger.start_attempt", fail_setup)
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    attempt = response.json()
    assert attempt["state"] == "failed"
    expected_code = "PROJECT_CHECKOUT_FAILED" if failure == "checkout" else "ATTEMPT_START_FAILED"
    assert attempt["sub_reason"] == expected_code
    assert attempt["ended_at"] is not None
    assert attempt["rc"] is None  # No process exit code exists when setup fails.
    assert attempt["event_cursor_tip"] == 2
    assert (await client.get("/v1/deployments/dep")).json()["state"] == "failed"
    assert (await client.get("/v1/deployments/dep/attempts")).json()["items"] == [attempt]
    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep") is None
    events = list(EventsReader(ws / "events.jsonl").read_range())
    assert [event["event_type"] for event in events] == ["attempt_start", "attempt_end"]
    terminal = events[-1]
    assert terminal["attempt_id"] == attempt["id"]
    assert terminal["deployment_id"] == "dep"
    assert terminal["payload"]["terminal_state"] == "failed"
    assert terminal["payload"]["code"] == expected_code
    assert terminal["payload"]["message"]
    assert "secret-token" not in (ws / "events.jsonl").read_text()


@pytest.mark.asyncio
async def test_setup_failure_remains_terminal_if_event_file_cannot_be_written(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api
    # A real filesystem failure: the log path was replaced with a directory.
    (ws / "events.jsonl").mkdir()

    async def fail_setup(session, *, attempt):
        await acquire_lock(session, deployment_id="dep", owner=f"attempt-{attempt.id}", interval_s=30)
        await session.commit()
        raise OSError("workspace unavailable")

    monkeypatch.setattr("app.core.deploy_trigger.start_attempt", fail_setup)
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "failed"
    assert response.json()["ended_at"] is not None
    assert response.json()["event_cursor_tip"] == 0
    assert (await client.get("/v1/deployments/dep")).json()["state"] == "failed"
    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
async def test_active_workspace_lock_rejects_start_without_releasing_it(attempt_api):
    client, dbmod, ws = attempt_api
    async with dbmod.get_session_factory()() as session:
        await acquire_lock(session, deployment_id="dep", owner="attempt-other", interval_s=30)
        await session.commit()

    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ATTEMPT_IN_PROGRESS"
    async with dbmod.get_session_factory()() as session:
        assert (await session.get(WorkspaceLock, "dep")).owner == "attempt-other"
        assert (await session.get(Deployment, "dep")).current_attempt_id is None


@pytest.mark.asyncio
async def test_setup_failure_does_not_overwrite_a_newer_attempts_deployment_state(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api

    async def superseded_setup(session, *, attempt):
        dep = await session.get(Deployment, "dep")
        dep.current_attempt_id = "newer-attempt"
        dep.state = "running"
        await session.commit()
        raise RuntimeError("old attempt setup failed")

    monkeypatch.setattr("app.core.deploy_trigger.start_attempt", superseded_setup)
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "failed"
    dep = (await client.get("/v1/deployments/dep")).json()
    assert dep["current_attempt_id"] == "newer-attempt"
    assert dep["state"] == "running"


@pytest.mark.asyncio
async def test_setup_failure_preserves_cancellation_committed_during_checkout(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api

    async def cancelled_setup(session, *, attempt):
        await acquire_lock(session, deployment_id="dep", owner=f"attempt-{attempt.id}", interval_s=30)
        await session.commit()
        async with dbmod.get_session_factory()() as cancelling:
            row = await cancelling.get(Attempt, attempt.id)
            row.state = "cancelled"
            row.ended_at = datetime.now(timezone.utc)
            await cancelling.commit()
        raise RuntimeError("checkout stopped after cancellation")

    monkeypatch.setattr("app.core.deploy_trigger.start_attempt", cancelled_setup)
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "cancelled"
    assert response.json()["sub_reason"] is None
    assert (await client.get("/v1/deployments/dep")).json()["state"] == "cancelled"
    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep") is None
    events = list(EventsReader(ws / "events.jsonl").read_range())
    assert events[-1]["payload"]["terminal_state"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "deploying", "running", "cancelling"])
async def test_start_rejects_active_attempt_without_replacing_it(attempt_api, state):
    client, dbmod, ws = attempt_api
    async with dbmod.get_session_factory()() as session:
        session.add(Attempt(id="active", deployment_id="dep", scope="full", state=state))
        dep = await session.get(Deployment, "dep")
        dep.current_attempt_id = "active"
        dep.state = "deploying"
        await session.commit()
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ATTEMPT_IN_PROGRESS"
    dep = (await client.get("/v1/deployments/dep")).json()
    assert dep["current_attempt_id"] == "active"
    assert dep["state"] == "deploying"
    assert len((await client.get("/v1/deployments/dep/attempts")).json()["items"]) == 1


@pytest.mark.asyncio
async def test_concurrent_start_requests_create_only_one_attempt(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    responses = await asyncio.gather(
        client.post("/v1/deployments/dep/attempts", json={"scope": "full"}),
        client.post("/v1/deployments/dep/attempts", json={"scope": "full"}),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    items = (await client.get("/v1/deployments/dep/attempts")).json()["items"]
    assert len(items) == 1
    assert (await client.get("/v1/deployments/dep")).json()["current_attempt_id"] == items[0]["id"]


@pytest.mark.asyncio
async def test_successful_start_returns_state_written_by_runner_session(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api

    async def start_runner(session, *, attempt):
        from app.core.attempt_lifecycle import mark_attempt_running
        await mark_attempt_running(attempt_id=attempt.id, pid=1234, artifact_dir=ws / "runner")

    monkeypatch.setattr("app.core.deploy_trigger.start_attempt", start_runner)
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "deploying"


@pytest.mark.asyncio
async def test_cancelled_attempt_cannot_be_replaced_until_runner_releases_lock(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    async with dbmod.get_session_factory()() as session:
        session.add(Attempt(id="cancelled", deployment_id="dep", scope="full", state="cancelled"))
        dep = await session.get(Deployment, "dep")
        dep.current_attempt_id = "cancelled"
        dep.state = "deploying"
        await acquire_lock(session, deployment_id="dep", owner="attempt-cancelled", interval_s=30)
        await session.commit()
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 409, response.text
    assert (await client.get("/v1/deployments/dep")).json()["current_attempt_id"] == "cancelled"


@pytest.mark.asyncio
async def test_expired_lock_of_terminal_attempt_does_not_block_retry(attempt_api, monkeypatch):
    client, dbmod, ws = attempt_api
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    async with dbmod.get_session_factory()() as session:
        lock = await acquire_lock(session, deployment_id="dep", owner="attempt-old", interval_s=30)
        lock.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await session.commit()
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": "full"})
    assert response.status_code == 201, response.text
    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["rollback_all", "rollback_team", "team_reset", "failed_teams"])
async def test_pinned_scenario_rejects_unimplemented_scope_before_reserving_attempt(attempt_api, scope):
    client, dbmod, ws = attempt_api
    async with dbmod.get_session_factory()() as session:
        (await session.get(Deployment, "dep")).project_sha = "a" * 40
        await session.commit()
    response = await client.post("/v1/deployments/dep/attempts", json={"scope": scope})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "PROJECT_SCENARIO_SCOPE_UNSUPPORTED"
    assert (await client.get("/v1/deployments/dep")).json()["current_attempt_id"] is None
    assert (await client.get("/v1/deployments/dep/attempts")).json()["items"] == []
