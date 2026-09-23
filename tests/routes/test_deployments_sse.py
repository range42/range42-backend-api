"""/v1/deployments/:id/events SSE tests."""
import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
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
async def test_sse_replays_existing_events(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        from app.core.events import EventsWriter
        from app.core.models import Deployment, ProxmoxHost, Project, Source
        async with dbmod.get_session_factory()() as s:
            s.add(Source(id="s", provider="github", base_url="https://g.com",
                         auth_kind="none"))
            s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n",
                              token_ref="t"))
            await s.commit()
        async with dbmod.get_session_factory()() as s:
            s.add(Project(id="p", name="p", source_id="s",
                          branch_strategy="shared_repo_subdir"))
            await s.commit()
        async with dbmod.get_session_factory()() as s:
            ws = tmp_path / "ALPHA-demo"
            ws.mkdir(parents=True)
            s.add(Deployment(id="dep-1", codename="ALPHA", scenario_label="demo",
                             project_id="p", target_host_id="h", team_count=1,
                             state="pending", workspace_path=str(ws)))
            await s.commit()
        writer = EventsWriter(tmp_path / "ALPHA-demo" / "events.jsonl")
        for i in range(3):
            writer.append(
                {"event_type": "log_line", "payload": {"text": f"hello {i}"}},
                attempt_id="att-1",
                deployment_id="dep-1",
            )
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            async with c.stream(
                "GET", "/v1/deployments/dep-1/events?to_seq=3"
            ) as r:
                assert r.status_code == 200
                assert r.headers["cache-control"] == "no-cache"
                assert r.headers["x-accel-buffering"] == "no"
                lines = []
                async for line in r.aiter_lines():
                    lines.append(line)
                    # Stop once we've seen the final event.
                    if '"event_seq":3' in line:
                        break
                body = "\n".join(lines)
                assert '"event_seq":1' in body
                assert '"event_seq":2' in body
                assert '"event_seq":3' in body
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_sse_deployment_not_found(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.get("/v1/deployments/missing/events?to_seq=1")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()
