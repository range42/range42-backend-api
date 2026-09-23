"""Named API access is enforced before handlers and audited without request data."""
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from app.core import db
from app.core.auth import BearerAuthMiddleware
from app.core.config import Settings
from app.core.models import Base

TOKENS = {role: f"public-test-{role}-" + "x" * 40 for role in ("admin", "operator", "viewer")}


def write_principals(tmp_path, rows=None):
    path = tmp_path / "principals.json"
    path.write_text(json.dumps({"version": 1, "principals": rows or [
        {"id": role + "-user", "role": role, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
        for role, token in TOKENS.items()
    ]}))
    path.chmod(0o600)
    return path


def test_operator_file_loads_hashed_tokens_without_exposing_them(tmp_path):
    from app.core.access import configured_principals
    settings = replace(Settings(), api_principals_file=str(write_principals(tmp_path)))
    principals = configured_principals(settings)
    assert {row.actor_id for row in principals.values()} == {"admin-user", "operator-user", "viewer-user"}
    assert all(token not in repr(principals) for token in TOKENS.values())


@pytest.mark.parametrize("patch", [{"role": "owner"}, {"id": "../user"}, {"token_sha256": "short"}, {"token": "plaintext"}])
def test_principal_configuration_rejects_invalid_fields(tmp_path, patch):
    from app.core.access import configured_principals
    row = {"id": "admin-user", "role": "admin", "token_sha256": "a" * 64, **patch}
    with pytest.raises(RuntimeError):
        configured_principals(replace(Settings(), api_principals_file=str(write_principals(tmp_path, [row]))))


@pytest.fixture
async def named_app(tmp_path, monkeypatch):
    from app.core.access import configured_principals
    from app.core.audit import AuditRecord
    from app.routes.v1.access import router
    engine = db.build_engine(f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
    factory = db.session_factory(engine)
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    principals = configured_principals(replace(Settings(), api_principals_file=str(write_principals(tmp_path))))
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token=None, principals=principals, audit_enabled=True)
    app.include_router(router, prefix="/v1")
    calls = []
    async def action(request: Request):
        calls.append(request.url.path)
        return {"actor": request.state.principal.actor_id}
    for path, methods in [("/v1/catalog/entries", ["GET"]), ("/v1/catalog/entries/{source_id}/{path:path}", ["GET"]), ("/v1/projects/{project_id}", ["PUT"]),
                          ("/v1/proxmox/hosts", ["POST"]), ("/v1/proxmox/hosts/{host_id}/vms/{vmid}/config", ["GET"]),
                          ("/v1/new-unreviewed-action", ["POST"]), ("/v0/admin/debug/ping", ["POST"])]:
        app.add_api_route(path, action, methods=methods)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, AuditRecord, calls, app
    await engine.dispose()


def headers(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


@pytest.mark.asyncio
async def test_named_identity_and_role_permissions_precede_routes(named_app):
    client, factory, model, calls, app = named_app
    response = await client.get("/v1/auth/me", headers=headers("viewer"))
    assert response.status_code == 200
    assert response.json()["actor_id"] == "viewer-user"
    assert response.json()["role"] == "viewer"
    assert (await client.get("/v1/catalog/entries", headers=headers("viewer"))).status_code == 200
    assert (await client.put("/v1/projects/p", headers=headers("viewer"), json={})).status_code == 403
    assert (await client.put("/v1/projects/p", headers=headers("operator"), json={})).status_code == 200
    assert (await client.post("/v1/proxmox/hosts", headers=headers("operator"))).status_code == 403
    assert (await client.get("/v1/proxmox/hosts/h/vms/100/config", headers=headers("viewer"))).status_code == 403
    assert (await client.post("/v0/admin/debug/ping", headers=headers("operator"))).status_code == 403
    assert (await client.post("/v1/new-unreviewed-action", headers=headers("operator"))).status_code == 403
    assert (await client.post("/v1/new-unreviewed-action", headers=headers("admin"))).status_code == 200
    assert calls == ["/v1/catalog/entries", "/v1/projects/p", "/v1/new-unreviewed-action"]
    assert (await client.get("/v1/catalog/entries/catalog/path/to/item", headers=headers("viewer"))).status_code == 200


@pytest.mark.asyncio
async def test_audit_records_actor_template_intent_and_result_without_request_secrets(named_app):
    client, factory, model, calls, app = named_app
    response = await client.put("/v1/projects/private-name?token=not-a-real-secret", headers=headers("operator"), json={"password": "never-in-audit"})
    assert response.status_code == 200
    async with factory() as session:
        row = (await session.scalars(select(model))).one()
        assert row.actor_id == "operator-user"
        assert row.route == "/v1/projects/{project_id}"
        assert row.status_code == 200
        assert row.state == "completed"
        assert row.finished_at is not None
    audit = await client.get("/v1/admin/audit", headers=headers("admin"))
    assert audit.status_code == 200
    assert audit.json()["total"] == 1
    assert all(value not in audit.text for value in ["private-name", "not-a-real-secret", "never-in-audit", TOKENS["operator"]])
    assert (await client.get("/v1/admin/audit", headers=headers("viewer"))).status_code == 403


@pytest.mark.asyncio
async def test_audit_unavailable_prevents_mutation_and_unknown_tokens_never_dispatch(named_app, monkeypatch):
    client, factory, model, calls, app = named_app
    from app.core import audit
    async def unavailable(*args, **kwargs):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(audit, "begin_record", unavailable)
    assert (await client.put("/v1/projects/p", headers=headers("operator"), json={})).status_code == 503
    assert (await client.put("/v1/projects/p", headers={"Authorization": "Bearer unknown"}, json={})).status_code == 401
    assert calls == []


@pytest.mark.parametrize("bad", ["duplicate_identity", "duplicate_hash", "no_admin", "readable_file", "duplicate_json", "empty"])
def test_principal_configuration_rejects_ambiguous_or_public_files(tmp_path, monkeypatch, bad):
    from app.core.access import configured_principals
    path = write_principals(tmp_path)
    value = json.loads(path.read_text())
    if bad == "duplicate_identity":
        value["principals"][1]["id"] = value["principals"][0]["id"]
    if bad == "duplicate_hash":
        value["principals"][1]["token_sha256"] = value["principals"][0]["token_sha256"]
    if bad == "no_admin":
        value["principals"][0]["role"] = "viewer"
    if bad == "empty":
        value["principals"] = []
    path.write_text(json.dumps(value))
    if bad == "readable_file":
        path.chmod(0o644)
    if bad == "duplicate_json":
        path.write_text('{"version":1,"version":1,"principals":[]}')
    with pytest.raises(RuntimeError):
        configured_principals(replace(Settings(), api_principals_file=str(path), api_token="", api_token_file=""))


@pytest.mark.asyncio
async def test_mutation_intent_is_visible_inside_handler_before_external_work(named_app):
    client, factory, model, calls, app = named_app
    async def inspect():
        async with factory() as session:
            row = (await session.scalars(select(model))).one()
            assert row.state == "started"
            assert row.status_code is None
        return {"ok": True}
    app.add_api_route("/v1/test-audit-order", inspect, methods=["POST"])
    response = await client.post("/v1/test-audit-order", headers=headers("admin"))
    assert response.status_code == 200
    assert len(response.headers["x-range42-audit-id"]) == 32


@pytest.mark.asyncio
async def test_failed_audit_completion_preserves_action_response_and_unfinished_intent(named_app, monkeypatch):
    client, factory, model, calls, app = named_app
    from app.core import audit
    async def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(audit, "finish_record", unavailable)
    response = await client.put("/v1/projects/p", headers=headers("operator"), json={})
    assert response.status_code == 200
    assert response.headers["x-range42-audit-state"] == "unconfirmed"
    assert len(calls) == 1
    async with factory() as session:
        assert (await session.scalars(select(model))).one().state == "started"


@pytest.mark.asyncio
async def test_denied_mutations_are_attributed_without_invoking_the_handler(named_app):
    client, factory, model, calls, app = named_app
    assert (await client.post("/v1/proxmox/hosts", headers=headers("viewer"))).status_code == 403
    async with factory() as session:
        row = (await session.scalars(select(model))).one()
        assert (row.actor_id, row.state, row.status_code) == ("viewer-user", "denied", 403)
    assert calls == []


def test_role_allowlists_reference_registered_route_templates():
    from app.main import create_app
    from app.core.access import VIEWER_READS, OPERATOR_READS, OPERATOR_WRITES
    app = create_app()
    routes = {(method, getattr(route, "path_format", route.path)) for route in app.routes for method in getattr(route, "methods", [])}
    assert OPERATOR_WRITES <= routes
    assert {("GET", route) for route in VIEWER_READS | OPERATOR_READS} <= routes


def test_named_configuration_can_replace_legacy_bearer_and_websockets_remain_protected(tmp_path, monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(main, "settings", replace(Settings(), auth_mode="required", api_token="", api_token_file="",
                        api_principals_file=str(write_principals(tmp_path))))
    app = main.create_app()
    async def socket(ws):
        await ws.accept()
        await ws.send_text("ok")
    app.add_websocket_route("/access-socket", socket)
    client = TestClient(app)
    assert client.get("/v1/auth/me", headers=headers("viewer")).json()["actor_id"] == "viewer-user"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/access-socket", headers=headers("viewer")):
            pass
    assert exc.value.code == 4403
    with client.websocket_connect("/access-socket", headers=headers("admin")) as ws:
        assert ws.receive_text() == "ok"


@pytest.mark.asyncio
async def test_permitted_routes_keep_framework_trailing_slash_redirects(named_app):
    client, factory, model, calls, app = named_app
    async def list_projects():
        return {"items": []}
    app.add_api_route("/v1/projects/", list_projects, methods=["GET", "POST"])
    response = await client.get("/v1/projects", headers=headers("viewer"), follow_redirects=True)
    assert response.status_code == 200
    response = await client.post("/v1/projects", headers=headers("operator"), json={}, follow_redirects=True)
    assert response.status_code == 200
    assert (await client.post("/v1/projects", headers=headers("viewer"), json={})).status_code == 403
