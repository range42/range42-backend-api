"""Bounded catalog snapshots avoid repeated clones without bypassing access checks."""
import asyncio
import threading

import pytest
from httpx import ASGITransport, AsyncClient

from app.routes.v1.catalog import entries
from app.schemas.v1.catalog import CatalogEntrySummary
from tests.routes.test_catalog_sources import _boot


@pytest.fixture
async def catalog(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    calls = []

    def load(source, repo):
        calls.append((source.id, repo.branch, source.token_ref))
        return [CatalogEntrySummary(source_id=source.id, path=f"labs/{i}",
                                    kind="lab", name=f"{repo.repo}-{i}", sha="a" * 40)
                for i in range(3)]

    monkeypatch.setattr(entries, "_entries_for_repo", load)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        source = (await client.post("/v1/catalog/sources", json={
            "provider": "github", "base_url": "https://github.com", "auth_kind": "pat", "token_ref": "private-one",
            "repos": [{"owner": "owner", "repo": "first", "branch": "main"}],
        })).json()
        try:
            yield client, app, source, calls
        finally:
            await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_pagination_filters_and_repeated_reads_share_one_exact_snapshot(catalog):
    client, _app, source, calls = catalog
    first = await client.get("/v1/catalog/entries", params={"limit": 1})
    second = await client.get("/v1/catalog/entries", params={"offset": 1, "limit": 2, "source_id": source["id"]})
    repeated = await client.get("/v1/catalog/entries", params={"kind": "lab"})
    assert first.status_code == second.status_code == repeated.status_code == 200
    assert len(calls) == 1
    assert [entry["path"] for entry in first.json()["items"] + second.json()["items"]] == ["labs/0", "labs/1", "labs/2"]
    assert {entry["sha"] for entry in repeated.json()["items"]} == {"a" * 40}


@pytest.mark.asyncio
async def test_concurrent_reads_share_clone_work_and_caller_cancellation(catalog, monkeypatch):
    client, _app, _source, calls = catalog
    entered, release = threading.Event(), threading.Event()
    original = entries._entries_for_repo

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(entries, "_entries_for_repo", blocked)
    first = asyncio.create_task(client.get("/v1/catalog/entries"))
    assert await asyncio.to_thread(entered.wait, 2)
    second = asyncio.create_task(client.get("/v1/catalog/entries"))
    await asyncio.sleep(0.05)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert (await second).status_code == 200
    assert len(calls) == 1
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_independent_sources_load_concurrently(catalog, monkeypatch):
    client, _app, _source, calls = catalog
    for name in ["second", "third"]:
        response = await client.post("/v1/catalog/sources", json={
            "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
            "repos": [{"owner": "owner", "repo": name}],
        })
        assert response.status_code == 201
    barrier = threading.Barrier(3)
    original = entries._entries_for_repo
    overlaps = []

    def load(*args):
        try:
            barrier.wait(timeout=1)
            overlaps.append(True)
        except threading.BrokenBarrierError:
            overlaps.append(False)
        return original(*args)

    monkeypatch.setattr(entries, "_entries_for_repo", load)
    result = await client.get("/v1/catalog/entries")
    assert result.status_code == 200
    assert result.json()["total"] == 9
    assert len(calls) == 3
    assert overlaps == [True, True, True]


@pytest.mark.asyncio
async def test_credentials_change_and_delete_cannot_reuse_old_private_snapshot(catalog):
    client, _app, source, calls = catalog
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    assert len(calls) == 1
    changed = await client.patch(f"/v1/catalog/sources/{source['id']}", json={"auth_kind": "pat", "token_ref": "private-two"})
    assert changed.status_code == 200
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    assert [token for _, _, token in calls] == ["private-one", "private-two"]
    assert (await client.delete(f"/v1/catalog/sources/{source['id']}")).status_code == 204
    assert (await client.get("/v1/catalog/entries")).json()["items"] == []


@pytest.mark.asyncio
async def test_source_refresh_drops_previous_snapshot_even_if_refresh_fails(catalog, monkeypatch):
    from app.routes.v1.catalog import refresh
    client, _app, source, calls = catalog
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    monkeypatch.setattr(refresh, "_count_entries_in_repo", lambda *_: 3)
    assert (await client.post(f"/v1/catalog/sources/{source['id']}/refresh")).status_code == 200
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    assert len(calls) == 2

    def failed(*_):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(refresh, "_count_entries_in_repo", failed)
    assert (await client.post(f"/v1/catalog/sources/{source['id']}/refresh")).status_code == 502
    monkeypatch.setattr(entries, "_entries_for_repo", failed)
    with pytest.raises(RuntimeError, match="unreachable"):
        await client.get("/v1/catalog/entries")


@pytest.mark.asyncio
async def test_operator_url_policy_is_rechecked_before_cached_read(catalog, monkeypatch):
    client, _app, _source, calls = catalog
    assert (await client.get("/v1/catalog/entries")).status_code == 200
    monkeypatch.setenv("RANGE42_GIT_ALLOWED_HOSTS", "gitlab.com")
    denied = await client.get("/v1/catalog/entries")
    assert denied.status_code == 400
    assert denied.json()["code"] == "GIT_URL_REJECTED"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_authenticated_snapshot_still_requires_gateway_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", "gateway-test-token-" + "x" * 32)
    from app.core.config import Settings
    from app import main
    monkeypatch.setattr(main, "settings", Settings())
    app, dbmod = await _boot(tmp_path, monkeypatch)
    calls = []

    def load(source, _repo):
        calls.append(True)
        return [CatalogEntrySummary(source_id=source.id, path="private", kind="lab", name="private entry", sha="a" * 40)]

    monkeypatch.setattr(entries, "_entries_for_repo", load)
    headers = {"Authorization": "Bearer gateway-test-token-" + "x" * 32}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.post("/v1/catalog/sources", headers=headers, json={
                "provider": "github", "base_url": "https://github.com", "auth_kind": "none",
                "repos": [{"owner": "owner", "repo": "private"}],
            })).status_code == 201
            assert (await client.get("/v1/catalog/entries", headers=headers)).json()["total"] == 1
            for wrong in [{}, {"Authorization": "Bearer incorrect"}]:
                response = await client.get("/v1/catalog/entries", headers=wrong)
                assert response.status_code == 401
                assert "private entry" not in response.text
            assert (await client.get("/v1/catalog/entries", headers=headers)).json()["total"] == 1
            assert len(calls) == 1
    finally:
        await dbmod.dispose_engine()
