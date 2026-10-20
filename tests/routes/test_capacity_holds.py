"""HTTP can submit only a server review stamp, never a capacity plan or receipt."""

from importlib.util import find_spec
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tests.core.test_capacity_ledger import ledger as _ledger
from tests.core.test_capacity_accounting import observation

ledger = _ledger


def source():
    assert find_spec("app.routes.v1.deployments.capacity") is not None, (
        "Missing canonical UI capacity reservation API"
    )
    from app.routes.v1.deployments import capacity

    return capacity


def transport(monkeypatch):
    routes = source()
    calls = []
    from app.core.preparation_transport import AgentConfiguration

    config = AgentConfiguration(
        "range42-preparation-fixture",
        Path("/synthetic-not-read"),
        "c" * 32,
        "pve-test",
        "f" * 64,
    )
    monkeypatch.setattr(routes, "agent_configuration", lambda *args: config)

    async def inspect(selected):
        assert selected == config
        calls.append("ssh")
        return observation()

    async def verify(host, observed):
        assert host.node_name == "pve-test" and observed.physical_node_id == "a" * 32
        calls.append("https")

    monkeypatch.setattr(routes, "inspect_capacity", inspect)
    monkeypatch.setattr(routes, "verify_api_peer", verify)
    return routes, calls


@pytest.mark.asyncio
async def test_review_reserve_reload_and_lost_reply_keep_one_pending_root_intent(
    ledger, monkeypatch
):
    mod, factory = ledger
    routes, calls = transport(monkeypatch)
    from app.main import create_app

    path = "/v1/deployments/" + "a" * 16 + "/capacity"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        initial = await client.get(path)
        assert initial.status_code == 200, initial.text
        assert initial.headers["cache-control"] == "no-store"
        assert initial.json()["hold"] is None and initial.json()["applicable"] is True
        assert calls == []
        inspected = await client.post(path + "/review", json={})
        assert inspected.status_code == 200, inspected.text
        body = inspected.json()
        assert body["assessment"]["admissible"] is True
        reviewed = {"review_revision": body["review_revision"]}
        first = await client.post(path, json=reviewed)
        assert first.status_code == 200, first.text
        assert first.json()["hold"]["status"] == "pending_root"
        assert (
            first.json()["execution_ready"]
            is first.json()["hold"]["root_accepted"]
            is False
        )
        again = await client.post(path, json=reviewed)
        assert again.status_code == 200 and again.json() == first.json()
        loaded = await client.get(path)
        assert loaded.json() == first.json()
        assert calls == ["ssh", "https"] * 3
    from app.core.capacity_models import CapacityHold
    from sqlalchemy import select, func

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(CapacityHold)) == 1


@pytest.mark.asyncio
async def test_capacity_api_is_admin_only_and_rejects_browser_resource_and_ready_overrides(
    ledger, monkeypatch
):
    routes, calls = transport(monkeypatch)
    from app.core.access import Principal, allowed

    for method, path in [
        ("GET", "/v1/deployments/{deployment_id}/capacity"),
        ("POST", "/v1/deployments/{deployment_id}/capacity"),
        ("POST", "/v1/deployments/{deployment_id}/capacity/review"),
    ]:
        for role in ["operator", "viewer"]:
            assert not allowed(Principal("staff", role), method, path)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for suffix, body in [
            ("/review", {"memory_mib": 1}),
            ("/review", {"execution_ready": True}),
            ("/review", {"command": "id"}),
            ("", {"review_revision": "0" * 64, "plan": {}}),
            ("", {"review_revision": "0" * 64, "root_accepted": True}),
        ]:
            response = await client.post(
                "/v1/deployments/" + "a" * 16 + "/capacity" + suffix, json=body
            )
            assert response.status_code == 422, response.text
        assert calls == []


@pytest.mark.asyncio
async def test_changed_native_generation_or_target_refuses_earlier_http_approval(
    ledger, monkeypatch
):
    mod, factory = ledger
    routes, calls = transport(monkeypatch)
    from app.main import create_app

    path = "/v1/deployments/" + "a" * 16 + "/capacity"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        inspected = await client.post(path + "/review", json={})
        assert inspected.status_code == 200, inspected.text

        async def changed(config):
            value = observation().model_copy(deep=True)
            value.resources.guests[0].configuration_revision = "9" * 64
            return value

        monkeypatch.setattr(routes, "inspect_capacity", changed)
        refused = await client.post(
            path, json={"review_revision": inspected.json()["review_revision"]}
        )
        assert refused.status_code == 409, refused.text

        assert (await client.get(path)).json()["hold"] is None

        async def changed_target(config):
            from app.core.models import ProxmoxHost

            async with factory() as session:
                host = await session.get(ProxmoxHost, "h")
                host.node_name = "changed"
                await session.commit()
            return observation()

        monkeypatch.setattr(routes, "inspect_capacity", changed_target)
        refused = await client.post(path + "/review", json={})
        assert refused.status_code == 409, refused.text


@pytest.mark.asyncio
async def test_release_unused_intent_keeps_original_allocation_and_never_grants_execution(ledger, monkeypatch):
    _, factory = ledger
    transport(monkeypatch)
    from app.main import create_app
    from app.core.allocation_models import DeploymentAllocation
    from sqlalchemy import select, func
    path = '/v1/deployments/' + 'a' * 16 + '/capacity'
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url='http://test') as client:
        review = (await client.post(path + '/review', json={})).json()
        assert (await client.post(path, json={'review_revision': review['review_revision']})).status_code == 200
        fresh = (await client.post(path + '/review', json={})).json()
        assert fresh.get('unused_retirement_eligible') is True, 'Missing independently reviewed unused-intent release'
        release = await client.request('DELETE', path, json={'review_revision': fresh['review_revision']})
        assert release.status_code == 200, release.text
        assert release.headers['cache-control'] == 'no-store'
        value = release.json()
        assert value['hold']['status'] == 'retired'
        assert value['hold']['retired_by'] == 'development'
        assert value['execution_ready'] is value['hold']['root_accepted'] is False
        assert (await client.get(path)).json() == value
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DeploymentAllocation)) == 2


@pytest.mark.asyncio
async def test_unused_release_has_no_browser_native_receipt_override_and_refuses_journaled_request(ledger, monkeypatch):
    mod, factory = ledger
    transport(monkeypatch)
    from app.main import create_app
    from app.core.access import Principal, allowed
    path = '/v1/deployments/' + 'a' * 16 + '/capacity'
    for role in ['operator', 'viewer']:
        assert not allowed(Principal('staff', role), 'DELETE', '/v1/deployments/{deployment_id}/capacity')
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url='http://test') as client:
        response = await client.request('DELETE', path, json={'review_revision': '0' * 64, 'native_request_id': None})
        assert response.status_code == 422
        approved = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
        row = await mod.reserve(factory, approved, actor='director')
        fresh = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
        await mod.journal_native_request(factory, fresh, actor='director', expected_revision=row.approval_revision)
        fresh = (await client.post(path + '/review', json={})).json()
        assert fresh['unused_retirement_eligible'] is False
        response = await client.request('DELETE', path, json={'review_revision': fresh['review_revision']})
        assert response.status_code == 409 and response.json()['code'] == 'CAPACITY_NATIVE_RECONCILIATION_REQUIRED'
