import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_workshop_operations_are_admin_only_and_disabled_without_agent(
    monkeypatch,
):
    from app.core.access import Principal, allowed

    assert not allowed(Principal("observer", "viewer"), "GET", "/v1/admin/workshop")
    assert not allowed(
        Principal("operator", "operator"), "POST", "/v1/admin/workshop/actions"
    )
    monkeypatch.delenv("RANGE42_WORKSHOP_SOCKET", raising=False)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/v1/admin/workshop")
        assert response.status_code == 503
        assert response.json()["code"] == "WORKSHOP_UNAVAILABLE"


@pytest.mark.asyncio
async def test_only_bounded_seat_actions_reach_the_privileged_worker(monkeypatch):
    from app.routes.v1.admin import workshop

    calls = []

    async def agent(value):
        calls.append(value)
        return {"operation_id": "abc", "state": "queued"}

    monkeypatch.setattr(workshop, "agent_request", agent)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for body in [
            {"action": "shell", "command": "id"},
            {"action": "bind", "username": "user11", "node_id": 4},
            {"action": "bind", "username": "user01", "node_id": 0},
            {"action": "reset", "username": "user01"},
        ]:
            response = await client.post("/v1/admin/workshop/actions", json=body)
            assert response.status_code == 422
        assert not calls
        response = await client.post(
            "/v1/admin/workshop/actions",
            json={"action": "bind", "username": "user01", "node_id": 5},
        )
        assert response.status_code == 202
        assert calls == [
            {"action": "bind", "username": "user01", "node_id": 5, "seats": None}
        ]


@pytest.mark.asyncio
async def test_roster_projection_excludes_credentials_and_unknown_provider_fields(
    monkeypatch,
):
    from app.routes.v1.admin import workshop

    async def agent(value):
        return {
            "id": "native-friday",
            "expires_at": "2026-10-23T18:00:00Z",
            "seats": [
                {
                    "username": "user01",
                    "proxmox_address": "10.77.32.2",
                    "deployer_address": "10.77.32.3",
                    "account_ready": True,
                    "key_ready": True,
                    "device": None,
                    "password": "SECRET",
                    "auth_key": "SECRET",
                }
            ],
            "operations": [],
            "password": "SECRET",
        }

    monkeypatch.setattr(workshop, "agent_request", agent)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/v1/admin/workshop")
        assert response.status_code == 200
        assert "SECRET" not in response.text
        assert "password" not in response.text and "auth_key" not in response.text


@pytest.mark.asyncio
async def test_credential_handout_is_private_and_has_no_url_token(monkeypatch):
    import base64
    from app.routes.v1.admin import workshop

    async def agent(value):
        assert value == {"action": "handout", "username": "user01"}
        return {"pdf": base64.b64encode(b"%PDF-1.4\nfixture").decode()}

    monkeypatch.setattr(workshop, "agent_request", agent)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/v1/admin/workshop/seats/user01/handout")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")
        assert response.headers["cache-control"] == "no-store"
        assert "user01.pdf" in response.headers["content-disposition"]
        assert (
            await client.get("/v1/admin/workshop/seats/../../secret/handout")
        ).status_code != 200


@pytest.mark.asyncio
async def test_replacing_a_lost_vpn_key_requires_confirmation_and_does_not_accept_secret_inputs(
    monkeypatch,
):
    from app.routes.v1.admin import workshop

    calls = []

    async def agent(value):
        calls.append(value)
        return {"operation_id": "replacement", "state": "queued"}

    monkeypatch.setattr(workshop, "agent_request", agent)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for body in [
            {"action": "replace_key", "username": "user10"},
            {"action": "replace_key", "username": "user10", "confirmation": "user09"},
            {
                "action": "replace_key",
                "username": "user10",
                "confirmation": "user10",
                "auth_key": "user-supplied",
            },
        ]:
            assert (
                await client.post("/v1/admin/workshop/actions", json=body)
            ).status_code == 422
        response = await client.post(
            "/v1/admin/workshop/actions",
            json={
                "action": "replace_key",
                "username": "user10",
                "confirmation": "user10",
            },
        )
        assert response.status_code == 202
        assert calls == [
            {
                "action": "replace_key",
                "username": "user10",
                "node_id": None,
                "seats": None,
            }
        ]
