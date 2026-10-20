"""HTTP never accepts a plan, actor or native receipt supplied by a browser."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.core.test_capacity_ledger import reserve_first
from tests.core.test_native_capacity_acceptance import receipt, ledger as _ledger
from tests.routes.test_capacity_holds import transport

ledger = _ledger


@pytest.mark.asyncio
async def test_http_journals_before_rpc_and_recovers_lost_acceptance_without_replaying(
    ledger, monkeypatch
):
    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    routes, calls = transport(monkeypatch)
    from app.core.capacity_models import CapacityHold
    from app.main import create_app

    sent = []
    durable = []

    async def native(config, *, action, request, **kwargs):
        async with factory() as session:
            saved = await session.get(CapacityHold, original.id)
            assert saved.native_request_id == request["request_id"]
            assert saved.native_receipt_json is None
        sent.append((action, request))
        if action == "capacity-admit":
            durable.append(receipt(request, fresh.observed))
            from app.core.preparation_transport import unavailable

            raise unavailable()
        assert action == "capacity-status"
        return durable[0]

    assert hasattr(routes, "read_agent"), (
        "Missing fixed original native acceptance route"
    )
    monkeypatch.setattr(routes, "read_agent", native)
    path = "/v1/deployments/" + original.deployment_id + "/capacity"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        accepted = await client.post(
            path + "/native", json={"review_revision": original.approval_revision}
        )
        assert accepted.status_code == 503, accepted.text
        loaded = await client.get(path)
        assert loaded.json()["hold"]["native_request_may_have_been_sent"] is True
        assert loaded.json()["hold"]["root_accepted"] is False
        assert loaded.json()["hold"]["native_acceptance_available"] is False
        assert loaded.json()["hold"]["native_reconciliation_available"] is True
        recovered = await client.post(path + "/native/reconcile", json={})
        assert recovered.status_code == 200, recovered.text
        body = recovered.json()
        assert (
            body["hold"]["root_accepted"] is True and body["execution_ready"] is False
        )
        assert body["hold"]["unused_retirement_review_available"] is False
        assert [action for action, _ in sent] == ["capacity-admit", "capacity-status"]
        assert sent[0][1] == sent[1][1]
        loaded = await client.get(path)
        assert loaded.json() == body
        assert calls == ["ssh", "https"] * 2
    async with factory() as session:
        assert len((await session.scalars(select(CapacityHold))).all()) == 1


@pytest.mark.asyncio
async def test_native_http_rejects_forged_fields_and_requires_admin(
    ledger, monkeypatch
):
    mod, factory = ledger
    original, _ = await reserve_first(mod, factory)
    routes, calls = transport(monkeypatch)
    from app.core.access import Principal, allowed
    from app.main import create_app

    template = "/v1/deployments/{deployment_id}/capacity/native"
    for role in ["viewer", "operator"]:
        assert not allowed(Principal("staff", role), "POST", template)
        assert not allowed(Principal("staff", role), "POST", template + "/reconcile")
    path = "/v1/deployments/" + original.deployment_id + "/capacity/native"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for extra in [
            {"request_id": "a" * 32},
            {"receipt": {}},
            {"actor": "admin"},
            {"execution_ready": True},
            {"guests": []},
        ]:
            denied = await client.post(
                path, json={"review_revision": original.approval_revision, **extra}
            )
            assert denied.status_code == 422, denied.text
            denied = await client.post(path + "/reconcile", json=extra)
            assert denied.status_code == 422, denied.text
        assert calls == []
