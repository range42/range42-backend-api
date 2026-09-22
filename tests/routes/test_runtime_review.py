"""Administration is role-bound and the reviewed target state cannot drift."""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tests.routes.test_project_scenario_execution import _boot, seed_scenario
from tests.core.test_runtime_operations import state
from tests.test_named_access import named_app as named_app, headers


@pytest.mark.asyncio
async def test_operator_cannot_invoke_host_firewall_through_guest_operation_endpoint(named_app):
    from app.routes.v1.deployments.runtime import router
    from app.core.errors import install_exception_handlers
    client, _, _, _, app = named_app
    install_exception_handlers(app)
    app.include_router(router, prefix="/v1/deployments")
    response = await client.post("/v1/deployments/missing/operations", headers=headers("operator"), json={
        "kind": "host_firewall", "enabled": False, "acknowledge_shared_scope": True,
    })
    assert response.status_code == 403
    assert response.json()["code"] == "RUNTIME_ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_host_operation_requires_review_and_rejects_changed_state(tmp_path, monkeypatch):
    from app.routes.v1.deployments import runtime
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    observed = state()
    observed["firewall"] = {"datacenter_enabled": False, "node_enabled": False, "errors": []}
    monkeypatch.setattr(runtime, "operation_profile", lambda kind: {"fingerprint": "a" * 64, "contract": "native-sdn-20260921", "dependencies": []})

    async def read(*args, **kwargs):
        return observed

    monkeypatch.setattr(runtime, "read_runtime_state", read)
    try:
        await seed_scenario(dbmod, tmp_path, extra_files={"manifest/scenario_vms.json": json.dumps({
            "version": 2, "vms": [{"vm_id": 3191, "vm_name": "owned-guest"}],
        })})
        body = {"kind": "host_firewall", "enabled": True, "acknowledge_shared_scope": True}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            missing = await client.post("/v1/deployments/dep-1/operations", json=body)
            assert missing.status_code == 409
            assert missing.json()["code"] == "RUNTIME_REVIEW_REQUIRED"
            preview = await client.post("/v1/deployments/dep-1/operations/plan", json=body)
            assert preview.status_code == 200, preview.text
            plan = preview.json()
            assert plan["plan"]["shared_scope"] == "datacenter_and_selected_node"
            assert plan["target_host_id"] == "h"
            assert plan["review_fingerprint"]
            observed["firewall"]["datacenter_enabled"] = True
            stale = await client.post("/v1/deployments/dep-1/operations", json={**body, "review_fingerprint": plan["review_fingerprint"]})
            assert stale.status_code == 409
            assert stale.json()["code"] == "RUNTIME_REVIEW_CHANGED"
            fresh = (await client.post("/v1/deployments/dep-1/operations/plan", json=body)).json()
            accepted = await client.post("/v1/deployments/dep-1/operations", json={**body, "review_fingerprint": fresh["review_fingerprint"]})
            assert accepted.status_code == 201, accepted.text
            assert accepted.json()["operation"]["authorized_role"] == "admin"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_report_exposes_only_same_target_and_revision_native_observation(tmp_path, monkeypatch):
    from app.routes.v1.deployments import runtime
    from app.core.models import Attempt, Deployment, ProxmoxHost
    app, dbmod = await _boot(tmp_path, monkeypatch)

    async def report(*args, **kwargs):
        return {"version": 1, "deployment_id": "dep-1", "target_host_id": "h", "node_name": "pve",
                "observed_at": "2026-09-22T10:00:00Z", "partial": False, "chains": [], "cards": [], "live_nat": {"available": False},
                "switches": {"datacenter_enabled": None, "node_enabled": None, "errors": []},
                "sdn": {"pending_changes": None, "errors": []}, "networks": []}

    monkeypatch.setattr(runtime, "read_runtime_report", report)
    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            deployment = await session.get(Deployment, "dep-1")
            host = await session.get(ProxmoxHost, "h")
            operation = {"request": {"kind": "runtime_observe"}, "project_sha": deployment.project_sha,
                         "target_host_id": "h", "target_identity": runtime.target_identity(host)}
            session.add(Attempt(id="native-read", deployment_id="dep-1", scope="runtime", state="succeeded",
                                project_sha=deployment.project_sha, operation=operation,
                                operation_result={"live_nat": {"available": True, "rules": [], "observed_at": "2026-09-22T09:59:00Z"}}))
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/deployments/dep-1/runtime-report")
            assert response.status_code == 200, response.text
            assert response.json()["live_nat"]["attempt_id"] == "native-read"
            async with dbmod.get_session_factory()() as session:
                host = await session.get(ProxmoxHost, "h")
                host.node_name = "changed"
                await session.commit()
            changed = (await client.get("/v1/deployments/dep-1/runtime-report")).json()
            assert changed["live_nat"]["available"] is False
            assert changed["live_nat"]["attempt_id"] is None
    finally:
        await dbmod.dispose_engine()
