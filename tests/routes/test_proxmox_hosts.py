"""/v1/proxmox/hosts CRUD + health probe tests."""
import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    import app.core.db as dbmod
    reload(dbmod)
    from app.core.models import Base
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.main import create_app
    return create_app(), dbmod


@pytest.mark.asyncio
async def test_create_and_delete_host(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=abc",
                    "default_bridge": "vmbr0",
                },
            )
            assert r.status_code == 201, r.text
            hid = r.json()["id"]
            r = await c.get("/v1/proxmox/hosts")
            assert r.status_code == 200
            assert any(h["id"] == hid for h in r.json()["items"])
            r = await c.delete(f"/v1/proxmox/hosts/{hid}")
            assert r.status_code == 204
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_delete_unknown_host_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.delete("/v1/proxmox/hosts/missing-host")
            assert r.status_code == 404
            body = r.json()
            assert set(body.keys()) >= {
                "error",
                "message",
                "code",
                "details",
                "trace_id",
                "timestamp",
            }
            assert body["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_health_unknown_host_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/v1/proxmox/hosts/bogus/health")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_health_unreachable_host_returns_unreachable_status(
    tmp_path, monkeypatch
):
    """When the Proxmox endpoint cannot be contacted (unreachable host in the
    default IANA 203.0.113.0/24 TEST-NET-3 range is used here via a
    localhost port that is almost certainly closed), the probe records
    status=unreachable and still returns 200 with the HostHealth payload.
    """
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve-off",
                    "api_url": "http://127.0.0.1:1",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=zzz",
                    "default_bridge": "vmbr0",
                },
            )
            hid = r.json()["id"]
            r = await c.get(f"/v1/proxmox/hosts/{hid}/health")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "unreachable"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_reseeding_the_same_host_updates_it_in_place(tmp_path, monkeypatch):
    """A scenario re-run POSTs the same name; it must not pile up duplicates.

    The id has to survive: deployments.target_host_id is a FK to it, so a new
    row per re-run would strand every earlier deployment on a stale host.
    """
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=abc",
                },
            )
            assert first.status_code == 201, first.text

            second = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01.lan:8006",
                    "node_name": "pve-node-2",
                    "token_ref": "r42@pam!tok=rotated",
                },
            )
            assert second.status_code == 200, second.text
            assert second.json()["id"] == first.json()["id"]
            # pydantic HttpUrl normalises a bare-host URL with a trailing slash
            assert second.json()["api_url"] == "https://pve01.lan:8006/"
            assert second.json()["node_name"] == "pve-node-2"

            listing = await c.get("/v1/proxmox/hosts")
            assert listing.json()["total"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_reseeding_refreshes_a_rotated_token(tmp_path, monkeypatch):
    """The stored PVE token must follow the re-seed, or deploys 401 forever."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=stale",
                },
            )
            await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=fresh",
                },
            )

        from sqlalchemy import select
        from app.core.models import ProxmoxHost
        async with dbmod.get_session_factory()() as session:
            stored = (
                await session.execute(select(ProxmoxHost.token_ref))
            ).scalars().all()
        assert stored == ["r42@pam!tok=fresh"]
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_a_second_host_under_a_different_name_is_still_created(
    tmp_path, monkeypatch
):
    """Upsert keys on name only — a genuinely new host must still register."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            a = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=a",
                },
            )
            b = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve02",
                    "api_url": "https://pve02:8006",
                    "node_name": "pve02",
                    "token_ref": "r42@pam!tok=b",
                },
            )
            assert b.status_code == 201, b.text
            assert b.json()["id"] != a.json()["id"]

            listing = await c.get("/v1/proxmox/hosts")
            assert listing.json()["total"] == 2
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_losing_the_race_on_a_new_name_still_upserts(tmp_path, monkeypatch):
    """Two concurrent registrations of the same new name must not 500.

    WEB_CONCURRENCY=1 keeps this to one process, but asyncio still interleaves
    across the await between the duplicate check and the commit: both requests
    can find nothing and both take the insert path. The loser of that race hits
    uq_proxmox_host_name and has to recover into the update path rather than
    surfacing an IntegrityError.
    """
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=winner",
                },
            )
            assert first.status_code == 201, first.text

            # Blind ONLY the pre-check, and only once: that is exactly what the
            # racing request sees. The recovery path re-reads and must find the
            # row the winner committed.
            import app.routes.v1.proxmox.hosts as hosts_mod

            real_lookup = hosts_mod._find_host_by_name
            seen = {"calls": 0}

            async def _blind_first_lookup(session, name):
                seen["calls"] += 1
                if seen["calls"] == 1:
                    return None
                return await real_lookup(session, name)

            monkeypatch.setattr(
                hosts_mod, "_find_host_by_name", _blind_first_lookup
            )

            second = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=loser",
                },
            )
            assert second.status_code == 200, second.text
            assert second.json()["id"] == first.json()["id"]

            listing = await c.get("/v1/proxmox/hosts")
            assert listing.json()["total"] == 1
    finally:
        await dbmod.dispose_engine()
