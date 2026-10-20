"""A retried durable controller job must never repeat a participant reset."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.models import Attempt, Deployment
from tests.core import test_workshop_ownership_import as ownership

env = ownership.env
BASE = "/v1/deployments/cohort/workshop/legacy/participants/user01"


@pytest.mark.asyncio
async def test_lost_reply_and_concurrent_dispatch_retain_one_parent_attempt(
    env, monkeypatch
):
    from app.main import create_app

    await ownership.save(env, await ownership.observe(env))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    body = {
        "action": "reset",
        "confirm_username": "user01",
        "dispatch_id": "20261020T120000-abcdef123456",
    }
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        first = await client.post(BASE + "/actions", json=body)
        assert first.status_code == 201, first.text
        attempt_id = first.json()["id"]
        async with env[1]() as session:
            attempt = await session.get(Attempt, attempt_id)
            attempt.state = "failed"
            await session.commit()
        replies = await asyncio.gather(
            *[client.post(BASE + "/actions", json=body) for _ in range(2)]
        )
        for reply in replies:
            assert reply.status_code == 201, reply.text
            assert (
                reply.json()["id"] == attempt_id and reply.json()["state"] == "failed"
            )
        changed = await client.post(
            BASE + "/actions", json={**body, "action": "configure"}
        )
        assert changed.status_code == 409, changed.text
        for invalid in [
            {**body, "parameters": {}},
            {**body, "confirm_username": "user02"},
        ]:
            assert (await client.post(BASE + "/actions", json=invalid)).status_code in {
                400,
                422,
            }
    async with env[1]() as session:
        assert await session.scalar(select(func.count()).select_from(Attempt)) == 1
        assert (await session.get(Deployment, "cohort")).native["parameters"][
            "WORKSHOP_ONLY_SEAT"
        ] == 0


@pytest.mark.asyncio
async def test_selected_preflight_uses_transient_seat_without_editing_parent(
    env, monkeypatch
):
    from app.main import create_app
    from app.routes.v1.deployments import preflight

    await ownership.save(env, await ownership.observe(env))
    observed = []

    async def evaluate(session, dep, payload):
        observed.append(
            (dep.id, dep.native["parameters"]["WORKSHOP_ONLY_SEAT"], payload.scope)
        )
        return {
            "id": "report",
            "deployment_id": dep.id,
            "attempt_id": None,
            "ts": "2026-10-20T12:00:00Z",
            "result": "pass",
            "checks": [],
        }

    monkeypatch.setattr(preflight, "evaluate_preflight", evaluate, raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(BASE + "/preflight", json={"action": "reset"})
        assert response.status_code == 200, response.text
        assert observed == [("cohort", 1, "reset")]
        assert (
            await client.post(
                BASE.replace("user01", "user06") + "/preflight",
                json={"action": "reset"},
            )
        ).status_code == 409
    async with env[1]() as session:
        assert (await session.get(Deployment, "cohort")).native["parameters"][
            "WORKSHOP_ONLY_SEAT"
        ] == 0
