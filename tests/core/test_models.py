import pytest
from sqlalchemy import select
from app.core.db import build_engine, session_factory
from app.core.models import (
    Base, Source, SourceRepo, Project, Deployment, Attempt,
    PreflightRecord, Snapshot, ProxmoxHost, WorkspaceLock,
)


@pytest.mark.asyncio
async def test_models_create_all_and_roundtrip(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'state.db'}"
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = session_factory(engine)
    async with Session() as s:
        src = Source(id="src-1", provider="github", base_url="https://github.com",
                     auth_kind="pat", token_ref="tok-1")
        s.add(src)
        host = ProxmoxHost(id="px-1", name="pve01", api_url="https://pve01:8006",
                           node_name="pve01", token_ref="px-tok",
                           default_bridge="vmbr0",
                           protected_vmids_override_json='[[100,101]]')
        s.add(host)
        await s.flush()
        repo = SourceRepo(id="rep-1", source_id="src-1", owner="range42",
                          repo="catalog-main", branch="main", manifest_path=None)
        s.add(repo)
        proj = Project(id="proj-1", name="p", source_id="src-1",
                       branch_strategy="shared_repo_subdir")
        s.add(proj)
        await s.flush()
        dep = Deployment(
            id="dep-1", codename="ALPHA", scenario_label="sc",
            project_id="proj-1", target_host_id="px-1", team_count=4,
            state="pending", workspace_path=str(tmp_path / "ALPHA-sc"),
        )
        s.add(dep)
        await s.flush()
        att = Attempt(id="att-1", deployment_id="dep-1", scope="full", state="pending")
        s.add(att)
        await s.flush()
        pre = PreflightRecord(id="pre-1", deployment_id="dep-1",
                              attempt_id="att-1", result="pass", checks_json="[]")
        s.add(pre)
        snap = Snapshot(id="snap-1", deployment_id="dep-1", vm_id=4010,
                        team_id=1, name="auto-deploy-att-1", kind="auto")
        s.add(snap)
        lock = WorkspaceLock(deployment_id="dep-1", owner="worker-1",
                             heartbeat_interval_s=30)
        s.add(lock)
        await s.commit()
    async with Session() as s:
        got = (await s.execute(select(Deployment))).scalar_one()
        assert got.codename == "ALPHA"
        assert got.team_count == 4
    await engine.dispose()
