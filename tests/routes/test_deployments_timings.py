import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.core.events import EventsWriter


@pytest.mark.asyncio
async def test_timings_derived_from_events(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from importlib import reload
    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)
    from app.core.models import Base, Deployment, Project, Source, ProxmoxHost
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ws = tmp_path / "A-b"
    ws.mkdir(parents=True)
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s", branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename="A", scenario_label="b", project_id="p",
                         target_host_id="h", team_count=1, state="completed",
                         workspace_path=str(ws)))
        await s.commit()
    w = EventsWriter(ws / "events.jsonl")
    w.append({"event_type": "phase_transition", "ts": "2026-04-14T10:00:00Z",
              "payload": {"from": None, "to": "network"}}, attempt_id="a")
    w.append({"event_type": "phase_transition", "ts": "2026-04-14T10:00:30Z",
              "payload": {"from": "network", "to": "router"}}, attempt_id="a")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/deployments/dep-1/timings")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert rows and rows[0]["stage"] == "network"
        assert rows[0]["duration_ms"] == 30000
