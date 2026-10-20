from pathlib import Path

import pytest
from sqlalchemy import select, func

from tests.routes.test_physical_nodes import client_node as _client_node
from tests.core.test_physical_capacity import capacity


client_node = _client_node


async def enrolled(client, monkeypatch):
    from app.routes.v1.proxmox import physical_nodes as routes
    from app.core.preparation_transport import AgentConfiguration
    from app.core.physical_nodes import NodeAttestation

    configuration = AgentConfiguration(
        "range42-preparation-test", Path("/synthetic"), "c" * 32, "pve-test", "f" * 64
    )
    monkeypatch.setattr(routes, "agent_configuration", lambda *args: configuration)

    async def inspect(config):
        return NodeAttestation.model_validate(capacity()["node"])

    async def verify(host, observed):
        assert observed.physical_node_id == "a" * 32

    monkeypatch.setattr(routes, "inspect_agent", inspect)
    monkeypatch.setattr(routes, "verify_api_peer", verify)
    reviewed = await client.post("/v1/proxmox/hosts/h/physical-node/review", json={})
    assert reviewed.status_code == 200, reviewed.text
    saved = await client.post(
        "/v1/proxmox/hosts/h/physical-node",
        json={"review_revision": reviewed.json()["revision"]},
    )
    assert saved.status_code == 200, saved.text
    return routes


@pytest.mark.asyncio
async def test_capacity_review_requires_an_original_current_binding_and_grants_no_allocation(
    client_node, monkeypatch
):
    client, db = client_node
    from app.core.access import Principal, allowed

    path = "/v1/proxmox/hosts/{host_id}/physical-node/capacity/review"
    assert allowed(Principal("admin", "admin"), "POST", path)
    assert not allowed(Principal("operator", "operator"), "POST", path)
    assert not allowed(Principal("viewer", "viewer"), "POST", path)
    before = await client.post(
        "/v1/proxmox/hosts/h/physical-node/capacity/review", json={}
    )
    assert before.status_code == 409, before.text
    routes = await enrolled(client, monkeypatch)
    assert hasattr(routes, "inspect_capacity"), "Missing node-bound capacity route"
    from app.core.physical_capacity import CapacityObservation

    async def inspect(config):
        return CapacityObservation.model_validate(capacity())

    monkeypatch.setattr(routes, "inspect_capacity", inspect)
    response = await client.post(
        "/v1/proxmox/hosts/h/physical-node/capacity/review", json={}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["summary"]["memory_headroom_mib"] == -8192
    assert response.json()["summary"]["allocation_granted"] is False
    assert "PRIVATE" not in response.text
    from app.core.allocation_models import DeploymentAllocation

    async with db.get_session_factory()() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DeploymentAllocation))
            == 0
        )
    invalid = await client.post(
        "/v1/proxmox/hosts/h/physical-node/capacity/review", json={"max_vcpus": 999999}
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["physical_id", "hardware", "root_policy", "target_during_read"]
)
async def test_capacity_refuses_changed_identity_policy_or_target(
    client_node, monkeypatch, damage
):
    client, db = client_node
    routes = await enrolled(client, monkeypatch)
    assert hasattr(routes, "inspect_capacity"), "Missing verified resource observation"
    from app.core.physical_capacity import CapacityObservation

    async def inspect(config):
        value = capacity()
        if damage == "physical_id":
            value["node"]["physical_node_id"] = "9" * 32
        elif damage == "hardware":
            value["node"]["hardware_binding"] = "9" * 64
        elif damage == "root_policy":
            value["node"]["configuration_revision"] = "9" * 64
        elif damage == "target_during_read":
            from app.core.models import ProxmoxHost

            async with db.get_session_factory()() as session:
                host = await session.get(ProxmoxHost, "h")
                host.node_name = "changed"
                await session.commit()
        return CapacityObservation.model_validate(value)

    monkeypatch.setattr(routes, "inspect_capacity", inspect)
    response = await client.post(
        "/v1/proxmox/hosts/h/physical-node/capacity/review", json={}
    )
    assert response.status_code == 409, response.text
