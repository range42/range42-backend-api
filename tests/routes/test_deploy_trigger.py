import asyncio
import pytest
from tests.fixtures.fake_runner import FakeRunner


@pytest.mark.asyncio
async def test_start_attempt_writes_events(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    from importlib import reload
    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)
    from app.core.models import (
        Base, Source, ProxmoxHost, Project, Deployment, Attempt,
    )
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ws = tmp_path / "A-b"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s", branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename="A", scenario_label="b", project_id="p",
                         target_host_id="h", team_count=1, state="pending",
                         workspace_path=str(ws)))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-1", deployment_id="dep-1", scope="full", state="pending"))
        await s.commit()
    from app.core.deploy_trigger import start_attempt
    runner = FakeRunner(script=[{"event_type": "task_end",
                                 "payload": {"task_name": "done"}}])
    from sqlalchemy import select
    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(select(Attempt).where(Attempt.id == "att-1"))).scalar_one()
        await start_attempt(s, attempt=att, runner=runner)
    await asyncio.sleep(0.3)
    assert (ws / "events.jsonl").exists()
    body = (ws / "events.jsonl").read_text()
    assert "attempt_start" in body
