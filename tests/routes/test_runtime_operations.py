"""Runtime controls expose desired state through a separate guarded endpoint."""
import pytest
from pydantic import TypeAdapter, ValidationError
from httpx import ASGITransport, AsyncClient


@pytest.mark.parametrize("body", [
    {"kind": "vm_firewall", "vm_id": 3191, "enabled": True},
    {"kind": "scenario_firewall", "enabled": False},
    {"kind": "sdn_snat", "vnet": "r42blue", "enabled": True, "acknowledge_shared_scope": True},
])
def test_operation_desired_state_is_typed(body):
    from app.schemas.v1.runtime import RuntimeOperation
    assert TypeAdapter(RuntimeOperation).validate_python(body).model_dump() == body


@pytest.mark.parametrize("body", [
    {"kind": "vm_firewall", "vm_id": 3191, "enabled": "false"},
    {"kind": "vm_firewall", "vm_id": True, "enabled": True},
    {"kind": "vm_firewall", "vm_id": 3191, "enabled": True, "bundle": "arbitrary"},
    {"kind": "scenario_firewall", "enabled": 1},
    {"kind": "sdn_snat", "vnet": "r42blue", "enabled": True},
    {"kind": "sdn_snat", "vnet": "r42blue", "enabled": True, "acknowledge_shared_scope": False},
    {"kind": "sdn_snat", "vnet": "r42blue", "enabled": True, "acknowledge_shared_scope": 1},
    {"kind": "sdn_snat", "vnet": "../bad", "enabled": True, "acknowledge_shared_scope": True},
    {"kind": "host_firewall", "enabled": True},
])
def test_ambiguous_or_broader_mutations_are_rejected(body):
    from app.schemas.v1.runtime import RuntimeOperation
    with pytest.raises(ValidationError):
        TypeAdapter(RuntimeOperation).validate_python(body)


def test_regular_attempt_route_cannot_accept_runtime_scope():
    from app.schemas.v1.deployments import AttemptCreate
    with pytest.raises(ValidationError):
        AttemptCreate(scope="runtime")


@pytest.mark.asyncio
async def test_runtime_status_and_operation_reservation_use_pinned_deployment(tmp_path, monkeypatch):
    from tests.routes.test_project_scenario_execution import _boot, seed_scenario
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    try:
        _, sha = await seed_scenario(dbmod, tmp_path, extra_files={
            "manifest/scenario_vms.json": '{"version":2,"vms":[{"vm_id":3191,"vm_name":"owned-guest"}]}',
        })
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/deployments/dep-1/runtime")
            assert response.status_code == 200, response.text
            assert response.json()["project_sha"] == sha
        from app.routes.v1.deployments import runtime
        monkeypatch.setattr(runtime, "operation_profile", lambda kind: {
            "fingerprint": "a" * 64, "dependencies": [],
        })
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/deployments/dep-1/operations", json={
                "kind": "vm_firewall", "vm_id": 3191, "enabled": True,
            })
            assert response.status_code == 201, response.text
            attempt = response.json()
            assert attempt["scope"] == "runtime"
            assert attempt["project_sha"] == sha
            assert attempt["operation"]["request"] == {"kind": "vm_firewall", "vm_id": 3191, "enabled": True}
            assert attempt["operation"]["runtime"]["fingerprint"] == "a" * 64
            assert attempt["operation_result"] is None
            duplicate = await client.post("/v1/deployments/dep-1/operations", json={
                "kind": "scenario_firewall", "enabled": False,
            })
            assert duplicate.status_code == 409
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_runtime_read_and_mutation_require_shared_operator_auth(tmp_path, monkeypatch):
    from tests.test_api_security import secured_app
    app = secured_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/v1/deployments/missing/runtime")).status_code == 401
        response = await client.post("/v1/deployments/missing/operations", json={"kind": "scenario_firewall", "enabled": True})
        assert response.status_code == 401
