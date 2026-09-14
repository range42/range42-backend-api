"""Snapshot lifetime, identity, bounded retention and invalidation contracts."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.catalog_snapshots import CatalogSnapshots, repository_key
from app.schemas.v1.catalog import CatalogEntrySummary


def row(name="current"):
    return CatalogEntrySummary(source_id="source", path="role", kind="ansible_role", name=name, sha="a" * 40)


@pytest.mark.asyncio
async def test_ttl_does_not_extend_on_access_and_returned_rows_are_independent():
    now = [0.0]
    cache = CatalogSnapshots(clock=lambda: now[0])
    calls = []

    def load():
        calls.append(True)
        return [row(str(len(calls)))]

    first = await cache.get("key", load)
    first[0].name = "caller mutation"
    now[0] = 29
    assert (await cache.get("key", load))[0].name == "1"
    now[0] = 30
    assert (await cache.get("key", load))[0].name == "2"


@pytest.mark.asyncio
async def test_expired_snapshot_is_not_a_fallback_for_failed_authorization_or_upstream_reads():
    now = [0.0]
    cache = CatalogSnapshots(clock=lambda: now[0])
    await cache.get("key", lambda: [row("private")])
    now[0] = 31

    def denied():
        raise ValueError("source denied")

    with pytest.raises(ValueError, match="source denied"):
        await cache.get("key", denied)
    assert not cache._snapshots
    assert (await cache.get("key", lambda: [row("recovered")]))[0].name == "recovered"


@pytest.mark.asyncio
async def test_invalidation_does_not_let_an_old_pending_load_refill_the_cache():
    import threading
    entered, release = threading.Event(), threading.Event()
    cache = CatalogSnapshots()

    def old():
        entered.set()
        assert release.wait(3)
        return [row("old")]

    pending = asyncio.create_task(cache.get("key", old))
    assert await asyncio.to_thread(entered.wait, 2)
    cache.invalidate()
    assert (await cache.get("key", lambda: [row("new")]))[0].name == "new"
    release.set()
    assert (await pending)[0].name == "old"
    assert (await cache.get("key", lambda: pytest.fail("should reuse new snapshot")))[0].name == "new"


@pytest.mark.asyncio
async def test_lru_and_serialized_metadata_size_bounds_do_not_truncate_results():
    cache = CatalogSnapshots(max_entries=2, max_bytes=1024)
    for key in ["one", "two", "one", "three"]:
        assert (await cache.get(key, lambda: [row(key)]))[0].name == key
    assert list(cache._snapshots) == ["one", "three"]
    large = [row("large" * 1024)]
    assert await cache.get("oversized", lambda: large) == large
    assert "oversized" not in cache._snapshots
    small_budget = CatalogSnapshots(max_bytes=len(row().model_dump_json().encode()))
    for key in ["one", "two"]:
        await small_budget.get(key, lambda: [row()])
    assert list(small_budget._snapshots) == ["two"]


def test_key_binds_every_source_repo_and_credential_identity_without_raw_credentials():
    source = SimpleNamespace(id="s", created_at="date", provider="github", base_url="https://github.com",
                             auth_kind="pat", token_ref="private-credential")
    repo = SimpleNamespace(id="r", owner="owner", repo="repo", branch="main", manifest_path=None, last_refreshed_at=None)
    original = repository_key(source, repo)
    assert len(original) == 64 and "private-credential" not in original
    for obj in [source, repo]:
        for field, value in vars(obj).copy().items():
            setattr(obj, field, f"{value}-changed")
            assert repository_key(source, repo) != original, field
            setattr(obj, field, value)
