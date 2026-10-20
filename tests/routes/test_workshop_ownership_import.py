
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func

from tests.core import test_workshop_ownership_import as core_tests
from app.core.allocation_models import DeploymentAllocation

env = core_tests.env


@pytest.mark.asyncio
async def test_import_is_reviewed_server_owned_and_recoverable_from_durable_status(
    env, monkeypatch
):
    from app.main import create_app
    from app.routes.v1.deployments import workshop_ownership as routes
    from app.routes.v1.admin import workshop

    mod, _, _, rpc = env

    async def observed(session, deployment_id):
        from app.core.models import ProxmoxHost

        host = await session.get(ProxmoxHost, "host")
        value = core_tests.proof()
        if rpc.import_id:
            value["handoff"] = {
                "import_id": rpc.import_id,
                "phase": rpc.phase,
                "review_revision": rpc.review_revision,
                "allocation_id": rpc.allocation_id,
            }
        return await mod.review_observation(
            session,
            deployment_id,
            mod.OwnershipWitness.model_validate(value),
            observed_target_revision=mod.target_revision(host),
        )

    monkeypatch.setattr(routes, "observe", observed)
    monkeypatch.setattr(workshop, "agent_request", rpc)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        url = "/v1/deployments/cohort/allocations/import"
        state = await client.get(url)
        assert state.status_code == 200, state.text
        assert (
            state.json()["phase"] == "unimported"
            and state.headers["cache-control"] == "no-store"
        )
        for value in [
            {"vm_ids": [43010]},
            {"ready": True},
            {"actor": "root"},
            {"path": "/tmp/receipts"},
        ]:
            assert (await client.post(url + "/review", json=value)).status_code == 422
        review = await client.post(url + "/review", json={})
        assert review.status_code == 200, review.text
        body = {"review_revision": review.json()["revision"]}
        for invalid in [
            {**body, "actor": "root"},
            {**body, "assignments": []},
            {**body, "ready": True},
        ]:
            assert (await client.post(url, json=invalid)).status_code == 422
        saved = await client.post(url, json=body)
        assert saved.status_code == 200, saved.text
        assert (
            saved.json()["phase"] == "active"
            and saved.json()["changed_by"] == "development"
        )
        assert saved.json()["capacity_borrowing_allowed"] is False
        recovered = await client.get(url)
        assert recovered.json()["import_id"] == saved.json()["import_id"]
        assert "PRIVATE" not in recovered.text
    async with env[1]() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DeploymentAllocation))
            == 1
        )


def test_import_control_is_administrator_only():
    from app.core.access import Principal, allowed

    path = "/v1/deployments/{deployment_id}/allocations/import"
    for role in ["viewer", "operator"]:
        for method, route in [
            ("GET", path),
            ("POST", path),
            ("POST", path + "/review"),
        ]:
            assert not allowed(Principal("person", role), method, route)


@pytest.mark.asyncio
async def test_verified_retirement_marks_the_import_released_and_disables_old_parent_actions(
    env, monkeypatch
):
    from app.main import create_app
    from app.routes.v1.deployments import allocations
    from app.core import legacy_workshop_operations as legacy
    from app.core.errors import Range42Error

    mod, factory, _, _ = env
    original = await core_tests.save(env, await core_tests.observe(env))

    async def absent(*args):
        return True

    monkeypatch.setattr(allocations, "vmid_is_free", absent)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        result = await client.delete("/v1/deployments/cohort/allocations")
        assert result.status_code == 204, result.text
        state = await client.get("/v1/deployments/cohort/allocations/import")
        assert state.status_code == 200, state.text
        assert (
            state.json()["phase"] == "released"
            and state.json()["import_id"] == original["import_id"]
        )
        assert [row["vm_id"] for row in state.json()["assignments"]] == list(
            range(43010, 43020)
        )
    async with factory() as session:
        with pytest.raises(Range42Error):
            await legacy.operation(session, "cohort", "user01", "deploy")
