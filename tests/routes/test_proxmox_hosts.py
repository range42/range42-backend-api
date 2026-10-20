"""/v1/proxmox/hosts CRUD + health probe tests."""
import pytest
import httpx
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
async def test_registered_host_health_uses_normalized_api_paths(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    paths = []
    def handle(request):
        paths.append(request.url.path)
        status = 200 if request.url.path in ("/api2/json/version", "/api2/json/cluster/sdn") else 500
        return httpx.Response(status, json={"data": {}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handle), **kwargs))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            created = await client.post("/v1/proxmox/hosts", json={
                "name": "pve01", "node_name": "pve01", "api_url": "https://pve01:8006",
                "token_ref": "r42@pam!tok=abc", "default_bridge": "vmbr0",
            })
            assert created.status_code == 201
            health = await client.get(f"/v1/proxmox/hosts/{created.json()['id']}/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["sdn_available"] is True
            assert paths == ["/api2/json/version", "/api2/json/cluster/sdn"]
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


@pytest.mark.asyncio
async def test_registered_host_health_verifies_tls_certificates(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    verifications = []
    original_client = httpx.AsyncClient
    def client_factory(**kwargs):
        verifications.append(kwargs.get("verify", True))
        return original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {}})), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    try:
        async with original_client(transport=ASGITransport(app=app), base_url="http://t") as client:
            created = await client.post("/v1/proxmox/hosts", json={
                "name": "pve-tls", "node_name": "pve-tls", "api_url": "https://pve-tls:8006",
                "token_ref": "r42@pam!tok=secret", "default_bridge": "vmbr0",
            })
            response = await client.get(f"/v1/proxmox/hosts/{created.json()['id']}/health")
            assert response.status_code == 200
        assert verifications and all(value is not False for value in verifications)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_explicit_credential_rotation_preserves_host_placement_and_protection(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            created = await client.post('/v1/proxmox/hosts', json={'name': 'scoped-workshops',
                'api_url': 'https://pve.test:8006', 'node_name': 'pve-a',
                'token_ref': 'workshop@pve!old=OLD-PRIVATE-CREDENTIAL', 'token_scope': 'workshop-only',
                'default_bridge': 'vmbr600', 'protected_vmids_override': [[100, 999], [43000, 43999]]})
            before = created.json()
            path = f"/v1/proxmox/hosts/{before['id']}/credential"
            review = await client.post(path + '/review')
            assert review.status_code == 200, review.text
            assert review.json()['host'] == before
            assert 'PRIVATE-CREDENTIAL' not in review.text
            rotated = await client.put(path, json={'expected_revision': review.json()['revision'],
                'token_ref': 'workshop@pve!renewed=NEW-PRIVATE-CREDENTIAL'})
            assert rotated.status_code == 200, rotated.text
            assert rotated.json() == before
            assert 'PRIVATE-CREDENTIAL' not in rotated.text
            from app.core.models import ProxmoxHost
            async with dbmod.get_session_factory()() as session:
                host = await session.get(ProxmoxHost, before['id'])
                assert host.token_ref == 'workshop@pve!renewed=NEW-PRIVATE-CREDENTIAL'
            stale = await client.put(path, json={'expected_revision': review.json()['revision'],
                'token_ref': 'workshop@pve!other=STALE-PRIVATE-CREDENTIAL'})
            assert stale.status_code == 409
            assert 'PRIVATE-CREDENTIAL' not in stale.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('api_url', 'https://other.test:8006'), ('node_name', 'other-node'),
    ('default_bridge', 'vmbr0'), ('protected_vmids_override', None)])
async def test_credential_rotation_cannot_change_other_target_fields(tmp_path, monkeypatch, field, value):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            created = await client.post('/v1/proxmox/hosts', json={'name': 'target',
                'api_url': 'https://pve.test:8006', 'node_name': 'pve-a', 'token_ref': 'r42@pve!old=PRIVATE-OLD'})
            path = f"/v1/proxmox/hosts/{created.json()['id']}/credential"
            review = await client.post(path + '/review')
            assert review.status_code == 200, review.text
            result = await client.put(path, json={'expected_revision': review.json()['revision'],
                'token_ref': 'r42@pve!new=PRIVATE-NEW', field: value})
            assert result.status_code == 422 and 'PRIVATE-NEW' not in result.text
    finally:
        await dbmod.dispose_engine()


def test_explicit_target_credential_changes_are_administrator_only():
    from app.core.access import Principal, allowed
    for method, path in [('POST', '/v1/proxmox/hosts/{host_id}/credential/review'),
                         ('PUT', '/v1/proxmox/hosts/{host_id}/credential')]:
        assert allowed(Principal('admin', 'admin'), method, path)
        assert not allowed(Principal('operator', 'operator'), method, path)
        assert not allowed(Principal('viewer', 'viewer'), method, path)


@pytest.mark.asyncio
@pytest.mark.parametrize('credential', ['PRIVATE-MALFORMED', 'r42@pve!token=', 'r42@pve!token=PRIVATE\nVALUE',
                                        'r42@pve!token=PRIVATE VALUE', 'r42!token=PRIVATE-VALUE'])
async def test_malformed_rotated_credentials_are_not_stored_or_returned(tmp_path, monkeypatch, credential):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            created = await client.post('/v1/proxmox/hosts', json={'name': 'target', 'api_url': 'https://pve.test:8006',
                'node_name': 'pve-a', 'token_ref': 'r42@pve!old=OLD-PRIVATE'})
            path = f"/v1/proxmox/hosts/{created.json()['id']}/credential"
            review = (await client.post(path + '/review')).json()
            result = await client.put(path, json={'expected_revision': review['revision'], 'token_ref': credential})
            assert result.status_code == 422
            assert 'PRIVATE' not in result.text
            unchanged = (await client.post(path + '/review')).json()
            assert unchanged['revision'] == review['revision']
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_simultaneous_credential_replacements_cannot_both_consume_one_review(tmp_path, monkeypatch):
    import asyncio
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            created = await client.post('/v1/proxmox/hosts', json={'name': 'target', 'api_url': 'https://pve.test:8006',
                'node_name': 'pve-a', 'token_ref': 'r42@pve!old=PRIVATE-OLD'})
            path = f"/v1/proxmox/hosts/{created.json()['id']}/credential"
            review = (await client.post(path + '/review')).json()
            results = await asyncio.gather(*(client.put(path, json={'expected_revision': review['revision'],
                'token_ref': 'r42@pve!new=PRIVATE-' + str(index)}) for index in range(2)))
            assert sorted(result.status_code for result in results) == [200, 409]
    finally:
        await dbmod.dispose_engine()
