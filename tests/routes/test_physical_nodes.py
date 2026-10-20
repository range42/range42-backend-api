import json

from httpx import ASGITransport, AsyncClient
import pytest

from tests.core.test_physical_nodes import proof
from tests.routes.test_deployments_preflight import _boot, _seed


@pytest.fixture
async def client_node(tmp_path, monkeypatch):
    app, db = await _boot(tmp_path, monkeypatch)
    await _seed(db, tmp_path)
    from app.core.models import ProxmoxHost

    async with db.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, "h")
        host.node_name = "pve-test"
        host.api_url = "https://pve.invalid:8006"
        host.token_ref = "synthetic@pve!key=PRIVATE-NODE-CREDENTIAL"
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, db
    await db.dispose_engine()


@pytest.mark.asyncio
async def test_inspection_is_optional_admin_only_and_cannot_claim_capacity(
    client_node, monkeypatch
):
    client, _ = client_node
    monkeypatch.delenv("RANGE42_PREPARATION_AGENTS_FILE", raising=False)
    from app.core.access import Principal, allowed

    for method, path in [
        ("GET", "/v1/proxmox/hosts/{host_id}/physical-node"),
        ("POST", "/v1/proxmox/hosts/{host_id}/physical-node/review"),
        ("POST", "/v1/proxmox/hosts/{host_id}/physical-node"),
    ]:
        for role in ["operator", "viewer"]:
            assert not allowed(Principal("staff", role), method, path)
    response = await client.get("/v1/proxmox/hosts/h/physical-node")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["binding"] is None
    assert response.json()["configuration_state"] == "unavailable"
    response = await client.post("/v1/proxmox/hosts/h/physical-node/review", json={})
    assert (
        response.status_code == 503
        and response.json()["code"] == "PREPARATION_AGENT_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_browser_cannot_submit_hardware_identity_paths_ready_flags_or_commands(
    client_node,
):
    client, _ = client_node
    for path, body in [
        ("review", {"physical_node_id": "a" * 32}),
        ("review", {"ssh_config": "/etc/ssh/config"}),
        ("review", {"execution_ready": True}),
        ("review", {"command": "id"}),
        ("", {"review_revision": "d" * 64, "attestation": proof()}),
        ("", {"review_revision": "d" * 64, "execution_ready": True}),
    ]:
        response = await client.post(
            "/v1/proxmox/hosts/h/physical-node" + ("/" + path if path else ""),
            json=body,
        )
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_inspect_review_save_and_lost_reply_recovery_use_only_server_observation(
    client_node, monkeypatch
):
    client, db = client_node
    from app.routes.v1.proxmox import physical_nodes as routes
    from app.core.preparation_transport import AgentConfiguration
    from app.core.physical_nodes import NodeAttestation
    from pathlib import Path

    config = AgentConfiguration(
        "range42-preparation-test",
        Path("/not-read-by-fixture"),
        "c" * 32,
        "pve-test",
        "f" * 64,
    )
    calls = []
    monkeypatch.setattr(routes, "agent_configuration", lambda *args: config)

    async def inspect(configuration):
        assert configuration == config
        calls.append("ssh")
        return NodeAttestation(**proof())

    async def verify(host, observed):
        assert (
            host.node_name == "pve-test" and observed.api_certificate_sha256 == "e" * 64
        )
        calls.append("https")

    monkeypatch.setattr(routes, "inspect_agent", inspect)
    monkeypatch.setattr(routes, "verify_api_peer", verify)
    review = await client.post("/v1/proxmox/hosts/h/physical-node/review", json={})
    assert review.status_code == 200, review.text
    assert review.json()["physical_node_id"] == "a" * 32
    assert "PRIVATE-NODE-CREDENTIAL" not in review.text
    body = {"review_revision": review.json()["revision"]}
    first = await client.post("/v1/proxmox/hosts/h/physical-node", json=body)
    assert first.status_code == 200, first.text
    again = await client.post("/v1/proxmox/hosts/h/physical-node", json=body)
    assert again.status_code == 200 and first.json() == again.json()
    assert calls == ["ssh", "https"] * 3
    saved = (await client.get("/v1/proxmox/hosts/h/physical-node")).json()
    assert saved["binding"]["physical_node_id"] == "a" * 32
    assert saved["binding"]["live_verified"] is False
    assert saved["binding"]["execution_ready"] is False
    assert saved["binding"]["alias_count"] == 1
    assert saved["needs_review"] is False
    assert "PRIVATE" not in json.dumps(saved)
