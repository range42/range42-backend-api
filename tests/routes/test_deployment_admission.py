import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.core import test_deployment_admission as admission_tests
from tests.core.test_deployment_admission import change
from app.core.models import Attempt, ProxmoxHost

env = admission_tests.env


@pytest.mark.asyncio
async def test_admission_routes_project_private_state_and_refuse_forged_fields(env):
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        url = "/v1/deployments/run/write-admission"
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["state"] == "open"
        request = {"action": "pause", "expected_revision": 0, "request_id": "1" * 32}
        for invalid in [
            {**request, "expected_revision": True},
            {**request, "actor": "root"},
            {**request, "vm_ids": [1]},
            {**request, "capacity_borrowing_allowed": True},
            {**request, "pause_id": "a" * 32},
        ]:
            assert (await client.post(url, json=invalid)).status_code == 422
        paused = await client.post(url, json=request)
        assert paused.status_code == 200, paused.text
        data = paused.json()
        assert data["changed_by"] == "development" and data["state"] == "closed"
        assert "PRIVATE" not in paused.text and "https://" not in paused.text
        request = {
            "action": "resume",
            "expected_revision": 1,
            "request_id": "2" * 32,
            "pause_id": data["pause_id"],
        }
        assert (await client.post(url, json=request)).status_code == 422
        assert (
            await client.post(url, json={**request, "confirmation": "yes"})
        ).status_code == 422
        assert (
            await client.post(url, json={**request, "confirmation": "RESUME"})
        ).json()["state"] == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("host_id", ["h", "alias"])
@pytest.mark.parametrize(
    "method,suffix,body",
    [
        *[
            ("POST", f"/status/{action}", None)
            for action in ["start", "stop", "shutdown", "suspend", "resume", "reboot"]
        ],
        ("DELETE", "", None),
        ("POST", "/snapshots", {"snapname": "test"}),
        ("DELETE", "/snapshots/test", None),
        ("POST", "/snapshots/test/rollback", None),
        ("PUT", "/config", {"digest": "a" * 64, "changes": {"memory": 1024}}),
        (
            "PUT",
            "/hardware/nics/net0",
            {"digest": "a" * 64, "changes": {"link_down": True}},
        ),
        ("PUT", "/hardware/disks/scsi0/grow", {"digest": "a" * 64, "size_gb": 64}),
    ],
)
async def test_all_raw_mutators_are_fenced_before_provider_io(
    env, method, suffix, body, host_id, monkeypatch
):
    _, factory, _, _ = env
    async with factory() as session:
        session.add(
            ProxmoxHost(
                id="alias",
                name="alias",
                api_url="https://another-alias.invalid",
                node_name="pve",
                token_ref="synthetic@pve!different=SECRET",
            )
        )
        await session.commit()
    await change(env)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        import httpx

        def no_provider_client(*args, **kwargs):
            raise AssertionError("A paused mutation attempted provider I/O")

        monkeypatch.setattr(httpx, "AsyncClient", no_provider_client)
        response = await client.request(
            method, f"/v1/proxmox/hosts/{host_id}/vms/45000{suffix}", json=body
        )
        assert response.status_code == 423, response.text
        assert response.json()["code"] == "DEPLOYMENT_WRITES_PAUSED"


@pytest.mark.asyncio
async def test_attempt_reservation_and_snapshot_set_dispatch_cannot_bypass_closed_admission(
    env,
):
    _, factory, _, _ = env
    await change(env)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/deployments/run/attempts", json={"scope": "full"}
        )
        assert result.status_code == 423, result.text
        result = await client.post(
            "/v1/deployments/run/snapshot-sets/unknown/execute",
            json={"plan_digest": "a" * 64},
        )
        assert result.status_code == 423, result.text
        result = await client.delete("/v1/deployments/run/allocations")
        assert result.status_code == 423, result.text
        assert (await client.get("/v1/deployments/run/allocations")).status_code == 200
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Attempt)) == 0


def test_only_administrators_can_change_admission():
    from app.core.access import Principal, allowed

    path = "/v1/deployments/{deployment_id}/write-admission"
    for role in ["operator", "viewer"]:
        assert allowed(Principal("person", role), "GET", path)
        assert not allowed(Principal("person", role), "POST", path)
    assert allowed(Principal("director", "admin"), "POST", path)


@pytest.mark.asyncio
async def test_unscoped_legacy_writers_cannot_bypass_a_closed_run(env):
    await change(env)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for path in [
            "/v0/admin/proxmox/vms/vm_id/start",
            "/v0/admin/run/bundles/generic/test/run",
            "/v0/admin/proxmox/network/create",
        ]:
            result = await client.post(path, json={})
            assert result.status_code == 423, result.text
            assert result.json()["code"] == "DEPLOYMENT_WRITES_PAUSED"


@pytest.mark.asyncio
async def test_pause_cannot_race_an_accepted_unscoped_legacy_writer(env):
    import asyncio
    from app.main import create_app

    app = create_app()
    entered, release = asyncio.Event(), asyncio.Event()

    @app.post("/v0/admin/test-writer")
    async def writer():
        entered.set()
        await release.wait()
        return {"completed": True}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        task = asyncio.create_task(client.post("/v0/admin/test-writer", json={}))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            result = await client.post(
                "/v1/deployments/run/write-admission",
                json={
                    "action": "pause",
                    "expected_revision": 0,
                    "request_id": "1" * 32,
                },
            )
            assert result.status_code == 409, result.text
            assert result.json()["code"] == "PROVISIONING_BUSY"
            assert (await client.get("/v1/deployments/run/write-admission")).json()[
                "state"
            ] == "open"
        finally:
            release.set()
            await task
