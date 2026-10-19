"""/v1/catalog/entries tests."""
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
async def test_entries_empty_returns_zero_page(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/v1/catalog/entries")
            assert r.status_code == 200
            assert r.json() == {
                "items": [],
                "total": 0,
                "offset": 0,
                "limit": 100,
            }
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_entry_detail_unknown_source_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/v1/catalog/entries/missing-src/some/path")
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
async def test_native_preview_is_bound_to_a_registered_source_and_exact_reviewed_commit(tmp_path, monkeypatch):
    app,dbmod=await _boot(tmp_path,monkeypatch)
    from app.routes.v1.catalog import entries
    calls=[]
    def preview(source,repo,path,sha):
        calls.append((source.id,repo.owner,repo.repo,repo.branch,path,sha))
        return {'revision':sha,'path':path,'files':{'scenarios/lab/main.yml':'---'},'truncated':False}
    monkeypatch.setattr(entries,'_native_preview_for_repo',preview)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://t') as client:
            source=(await client.post('/v1/catalog/sources',json={'provider':'github','base_url':'https://github.com',
                'auth_kind':'none','repos':[{'owner':'range42','repo':'range42-playbooks','branch':'dev'}]})).json()
            endpoint='/v1/catalog/sources/'+source['id']+'/native-preview'
            assert (await client.get(endpoint,params={'path':'scenarios/lab','sha':'main'})).status_code==422
            assert not calls
            result=await client.get(endpoint,params={'path':'scenarios/lab','sha':'a'*40})
            assert result.status_code==200
            assert result.json()['repository']=={'owner':'range42','repo':'range42-playbooks','branch':'dev'}
            assert calls==[(source['id'],'range42','range42-playbooks','dev','scenarios/lab','a'*40)]
            assert (await client.get('/v1/catalog/sources/unknown/native-preview',params={'path':'scenarios/lab'})).status_code==404
    finally:await dbmod.dispose_engine()
