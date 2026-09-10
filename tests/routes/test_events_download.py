"""Downloads contain a finite snapshot of canonical redacted events."""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tests.routes.test_deployments_sse import _boot


async def seed(dbmod, tmp_path):
    from app.core.models import Deployment, Project, ProxmoxHost, Source
    async with dbmod.get_session_factory()() as session:
        session.add(Source(id="s", provider="github", base_url="https://github.com", auth_kind="none"))
        session.add(ProxmoxHost(id="h", name="lab", api_url="https://pve.test", node_name="pve", token_ref="test"))
        await session.commit()
        session.add(Project(id="p", name="test", source_id="s", branch_strategy="shared_repo_subdir"))
        await session.commit()
        session.add(Deployment(id="dep", codename="LAB", scenario_label="demo", project_id="p", target_host_id="h", team_count=1, state="deploying", workspace_path=str(tmp_path / "workspace")))
        await session.commit()
    path = tmp_path / "workspace/events.jsonl"
    path.parent.mkdir()
    return path


@pytest.mark.asyncio
async def test_authenticated_finite_download_preserves_all_canonical_event_types(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        from app.core.events import EventsWriter
        path = await seed(dbmod, tmp_path)
        writer = EventsWriter(path)
        for result in ("ok", "skipped", "include"):
            writer.append({"event_type": "log_line", "payload": {"text": f"{result} [REDACTED]"}}, attempt_id="a", deployment_id="dep")
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
        from app.core.auth import BearerAuthMiddleware
        app.add_middleware(BearerAuthMiddleware, token="download-test-token")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/v1/deployments/dep/events/download")).status_code == 401
            response = await client.get("/v1/deployments/dep/events/download", headers={"Authorization": "Bearer download-test-token"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert response.headers["content-disposition"] == 'attachment; filename="events.jsonl"'
        assert [json.loads(line)["payload"]["text"] for line in response.text.splitlines()] == ["ok [REDACTED]", "skipped [REDACTED]", "include [REDACTED]"]
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_download_does_not_follow_event_file_symlink(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        path = await seed(dbmod, tmp_path)
        secret = tmp_path / "private.txt"
        secret.write_text('private fixture')
        path.symlink_to(secret)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/deployments/dep/events/download")
        assert response.status_code == 409
        assert 'private fixture' not in response.text
    finally:
        await dbmod.dispose_engine()
