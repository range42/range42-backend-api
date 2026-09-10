"""Repository onboarding persists usable sources without public credentials."""
import asyncio

import git
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.models import Source
from tests.routes.test_catalog_sources import _boot


@pytest.mark.asyncio
async def test_registered_public_repository_refreshes_and_browses(tmp_path, monkeypatch):
    # A real local Git tree with the public catalog's container and role formats.
    tree = tmp_path / "catalog"
    tree.mkdir()
    container = tree / "03_container_layer/docker/admin/demo"
    container.mkdir(parents=True)
    (container / "meta.json").write_text(
        '{"x_range42":{"exercise":{"id":"demo"},"catalog":{"tags":["training"]}}}'
    )
    (container / "README.md").write_text("# Demo container\n")
    role = tree / "02_ansible_layer/admin/roles/demo/meta"
    role.mkdir(parents=True)
    (role / "main.yml").write_text("galaxy_info:\n  description: Demo role\n")
    local = git.Repo.init(tree, initial_branch="main")
    local.index.add([str(path.relative_to(tree)) for path in tree.rglob("*") if path.is_file() and ".git" not in path.parts])
    actor = git.Actor("Test", "test@example.invalid")
    local.index.commit("Catalog fixtures", author=actor, committer=actor)
    clone_from = git.Repo.clone_from
    clone_urls = []

    def clone_local(url, destination, **kwargs):
        clone_urls.append(url)
        return clone_from(str(tree), destination, **kwargs)

    monkeypatch.setattr(git.Repo, "clone_from", clone_local)
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            created = await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [{"owner": "range42", "repo": "range42-catalog"}],
            })
            assert created.status_code == 201, created.text
            source = created.json()
            assert len(source.get("repos", [])) == 1
            assert source["repos"][0]["branch"] == "main"
            source_id = source["id"]
            refreshed = await client.post(f"/v1/catalog/sources/{source_id}/refresh")
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()["repos_seen"] == 1
            assert refreshed.json()["entries_indexed"] == 2
            listed = (await client.get("/v1/catalog/sources")).json()["items"][0]
            assert listed["repos"][0]["last_refreshed_at"] is not None
            entries = await client.get("/v1/catalog/entries", params={"source_id": source_id})
            assert entries.status_code == 200, entries.text
            assert entries.json()["total"] == 2
            assert {entry["kind"] for entry in entries.json()["items"]} == {"container", "ansible_role"}
            assert {entry["sha"] for entry in entries.json()["items"]} == {local.head.commit.hexsha}
            detail = await client.get(f"/v1/catalog/entries/{source_id}/03_container_layer/docker/admin/demo")
            assert detail.status_code == 200, detail.text
            assert detail.json()["readme_md"] == "# Demo container\n"
            assert detail.json()["sha"] == local.head.commit.hexsha
            assert clone_urls == ["https://github.com/range42/range42-catalog.git"] * 3
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_default_catalog_registration_is_idempotent(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            first = await client.post("/v1/catalog/sources/default")
            assert first.status_code == 200, first.text
            second = await client.post("/v1/catalog/sources/default")
            assert second.json() == first.json()
            source = first.json()
            assert source["provider"] == "github"
            assert source["base_url"].rstrip("/") == "https://github.com"
            assert source["auth_kind"] == "none"
            assert source["has_token"] is False
            assert len(source["repos"]) == 1
            assert source["repos"][0]["owner"] == "range42"
            assert source["repos"][0]["repo"] == "range42-catalog"
            assert source["repos"][0]["branch"] == "main"
            assert (await client.get("/v1/catalog/sources")).json()["total"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_default_catalog_reuses_existing_public_source(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            existing = (await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
            })).json()
            default = await client.post("/v1/catalog/sources/default")
            assert default.status_code == 200, default.text
            assert default.json()["id"] == existing["id"]
            assert len(default.json()["repos"]) == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_default_catalog_preserves_other_repository_sources(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            existing = (await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [{"owner": "range42", "repo": "other-catalog"}],
            })).json()
            default = await client.post("/v1/catalog/sources/default")
            assert default.status_code == 200, default.text
            assert default.json()["id"] != existing["id"]
            sources = (await client.get("/v1/catalog/sources")).json()["items"]
            assert next(source for source in sources if source["id"] == existing["id"]) == existing
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_new_sources_require_one_repository_per_source(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [{"owner": "range42", "repo": "first"}, {"owner": "range42", "repo": "second"}],
            })
            assert response.status_code == 422
            assert (await client.get("/v1/catalog/sources")).json()["total"] == 0
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_concurrent_default_registration_has_one_source(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            responses = await asyncio.gather(
                client.post("/v1/catalog/sources/default"),
                client.post("/v1/catalog/sources/default"),
            )
            assert all(response.status_code == 200 for response in responses)
            assert responses[0].json()["id"] == responses[1].json()["id"]
            assert (await client.get("/v1/catalog/sources")).json()["total"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"auth_kind": "pat", "token_ref": "   "},
    {"base_url": "https://token@github.com"},
    {"base_url": "https://github.com?token=SECRET"},
    {"base_url": "https://github.com#SECRET"},
])
async def test_invalid_source_credentials_are_rejected(tmp_path, monkeypatch, fields):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none", **fields,
            })
            assert response.status_code == 422
            assert "SECRET" not in response.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_credentials_rotate_and_clear_without_exposure(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            source = (await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
            })).json()
            url = f"/v1/catalog/sources/{source['id']}"
            rotated = await client.patch(url, json={"auth_kind": "pat", "token_ref": "ghp_SECRET"})
            assert rotated.status_code == 200, rotated.text
            assert rotated.json()["has_token"] is True
            assert "ghp_SECRET" not in rotated.text
            assert "token_ref" not in rotated.json()
            cleared = await client.patch(url, json={"auth_kind": "none"})
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["has_token"] is False
            async with dbmod.get_session_factory()() as session:
                stored = (await session.execute(select(Source).where(Source.id == source["id"]))).scalar_one()
                assert stored.token_ref is None
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_credential_patch_unknown_source_and_missing_token(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            missing = await client.patch("/v1/catalog/sources/missing", json={"auth_kind": "none"})
            assert missing.status_code == 404
            for token in (None, "", "   "):
                invalid = await client.patch("/v1/catalog/sources/missing", json={"auth_kind": "pat", "token_ref": token})
                assert invalid.status_code == 422
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("repository", [
    {"owner": "../range42", "repo": "catalog"},
    {"owner": "range42", "repo": "../catalog"},
    {"owner": "range42", "repo": "catalog.git"},
    {"owner": "range42", "repo": "catalog", "branch": "--upload-pack=evil"},
    {"owner": "range42", "repo": "catalog", "branch": "../main"},
    {"owner": "range42", "repo": "catalog", "branch": "bad branch"},
])
async def test_invalid_repository_is_rejected(tmp_path, monkeypatch, repository):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [repository],
            })
            assert response.status_code == 422, response.text
            assert (await client.get("/v1/catalog/sources")).json()["total"] == 0
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_duplicate_repository_in_source_is_validation_error(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [{"owner": "range42", "repo": "catalog"}] * 2,
            })
            assert response.status_code == 422, response.text
    finally:
        await dbmod.dispose_engine()
