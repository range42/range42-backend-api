"""Browser project registration preserves identity without copying credentials."""
import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.models import Deployment, Project, ProxmoxHost, Source
from tests.routes.test_projects import _boot


@pytest_asyncio.fixture
async def registration_api(tmp_path, monkeypatch):
    app, db = await _boot(tmp_path, monkeypatch)
    async with db.get_session_factory()() as session:
        session.add(Source(id="git", provider="gitlab", base_url="https://gitlab.example",
                           auth_kind="pat", token_ref="private-git-token"))
        session.add(Source(id="other", provider="github", base_url="https://github.com", auth_kind="none"))
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as client:
            yield client, db
    finally:
        await db.dispose_engine()


def binding(**changes):
    return {"name": "Browser lab", "source_id": "git", "branch_strategy": "shared_repo_subdir",
            "repo_owner": "group/team", "repo_name": "projects", "subdir": "labs/browser", **changes}


@pytest.mark.asyncio
async def test_put_registers_stable_browser_identity_and_get_does_not_expose_credentials(registration_api):
    client, db = registration_api
    first = await client.put("/v1/projects/browser-123", json=binding())
    assert first.status_code == 201, first.text
    second = await client.put("/v1/projects/browser-123", json=binding(name="Renamed lab"))
    assert second.status_code == 200, second.text
    assert second.json()["id"] == "browser-123"
    assert second.json()["created_at"] == first.json()["created_at"]
    read = await client.get("/v1/projects/browser-123")
    assert read.json() == second.json()
    assert "private-git-token" not in read.text
    assert not any(key in read.json() for key in ("token", "token_ref", "password", "base_url"))
    assert (await client.get("/v1/projects/")).json()["total"] == 1


@pytest.mark.asyncio
async def test_concurrent_registration_creates_one_project(registration_api):
    client, db = registration_api
    replies = await asyncio.gather(*[client.put("/v1/projects/browser-123", json=binding()) for _ in range(2)])
    assert sorted(reply.status_code for reply in replies) == [200, 201]
    assert (await client.get("/v1/projects/")).json()["total"] == 1


@pytest.mark.asyncio
async def test_patch_accepts_partial_fields_and_preserves_binding(registration_api):
    client, db = registration_api
    await client.put("/v1/projects/browser-123", json=binding())
    response = await client.patch("/v1/projects/browser-123", json={"name": "Changed"})
    assert response.status_code == 200, response.text
    assert response.json()["repo_owner"] == "group/team"
    assert response.json()["name"] == "Changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url", [("POST", "/v1/projects/"), ("PUT", "/v1/projects/browser-123")])
async def test_registration_rejects_unknown_source_before_database_write(registration_api, method, url):
    client, db = registration_api
    response = await client.request(method, url, json=binding(source_id="missing"))
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "SOURCE_NOT_FOUND"
    assert (await client.get("/v1/projects/")).json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"name": " "}, {"repo_owner": "group/../other"}, {"repo_name": "other.git"},
    {"repo_name": "../other"}, {"subdir": "../outside"}, {"subdir": "/outside"},
    {"subdir": "safe/.git/config"}, {"subdir": "safe\\outside"}, {"token_ref": "do-not-store"},
])
async def test_registration_validates_binding_and_rejects_credential_fields(registration_api, changes):
    client, db = registration_api
    response = await client.put("/v1/projects/browser-123", json=binding(**changes))
    assert response.status_code == 422, response.text
    assert (await client.get("/v1/projects/")).json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"source_id": "other", "repo_owner": "group"}, {"repo_name": "changed"},
    {"subdir": "labs/changed"}, {"branch_strategy": "dedicated_repo"},
])
async def test_existing_deployments_prevent_rebinding_but_allow_rename(registration_api, tmp_path, changes):
    client, db = registration_api
    await client.put("/v1/projects/browser-123", json=binding())
    async with db.get_session_factory()() as session:
        session.add(ProxmoxHost(id="host", name="pve", api_url="https://pve:8006", node_name="pve", token_ref="t"))
        await session.commit()
        session.add(Deployment(id="dep", codename="LAB", scenario_label="demo", project_id="browser-123",
                               target_host_id="host", workspace_path=str(tmp_path)))
        await session.commit()
    response = await client.put("/v1/projects/browser-123", json=binding(**changes))
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PROJECT_BINDING_IN_USE"
    renamed = await client.patch("/v1/projects/browser-123", json={"name": "Allowed rename"})
    assert renamed.status_code == 200, renamed.text
    async with db.get_session_factory()() as session:
        row = await session.get(Project, "browser-123")
        assert row.repo_name == "projects"
        assert row.subdir == "labs/browser"
