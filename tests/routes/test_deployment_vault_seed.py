"""Creating a deployment seeds <ws>/secrets/vault_pass.txt from the payload.

`deploy_trigger` reads that file to set ANSIBLE_VAULT_PASSWORD_FILE and to
unlock the workspace SSH keys (#112). Nothing else wrote it, so a deployment
created through the API could not decrypt anything — the UI collects the
password on the deploy form and it had nowhere to go.
"""
import stat

import pytest
from httpx import ASGITransport, AsyncClient

VAULT_PW = "correct-horse-battery-staple"


async def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from importlib import reload

    from app.core import config as cfg
    reload(cfg)
    import app.core.db as dbmod
    reload(dbmod)
    # workspace.py does `from app.core.config import settings`, binding the
    # object at import — without this reload it keeps the previous test's
    # workspace_root and writes into the wrong tmp_path.
    import app.core.workspace as wsmod
    reload(wsmod)
    import app.routes.v1.deployments.crud as crudmod
    reload(crudmod)
    from app.core.models import Base

    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.core.models import Project, ProxmoxHost, Source
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s1", provider="github",
                     base_url="https://github.com", auth_kind="none"))
        s.add(ProxmoxHost(id="h1", name="pve", api_url="https://10.0.0.5:8006",
                          node_name="pve", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p1", name="proj", source_id="s1",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()

    from app.main import create_app
    return create_app()


def _payload(**over):
    body = {
        "codename": "ALPHA",
        "scenario_label": "demo_lab",
        "project_id": "p1",
        "target_host_id": "h1",
        "team_count": 1,
    }
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_vault_password_is_written_to_the_workspace(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/deployments/",
                         json=_payload(secrets={"vault_password": VAULT_PW}))
    assert r.status_code == 201, r.text

    ws = tmp_path / "ws" / "ALPHA-demo_lab"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    assert vault_pass.is_file(), "deploy_trigger reads this path"
    assert vault_pass.read_text() == VAULT_PW


@pytest.mark.asyncio
async def test_vault_password_file_is_not_world_readable(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        await c.post("/v1/deployments/",
                     json=_payload(secrets={"vault_password": VAULT_PW}))

    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"
    mode = stat.S_IMODE(vault_pass.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.asyncio
async def test_no_secrets_leaves_no_file(tmp_path, monkeypatch):
    """An operator-seeded workspace must not get an empty file written over it."""
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/deployments/", json=_payload())
    assert r.status_code == 201, r.text
    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"
    assert not vault_pass.exists()


@pytest.mark.asyncio
async def test_empty_vault_password_is_ignored(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        await c.post("/v1/deployments/", json=_payload(secrets={"vault_password": ""}))
    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"
    assert not vault_pass.exists()


@pytest.mark.asyncio
async def test_vault_password_is_not_echoed_in_the_response(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/deployments/",
                         json=_payload(secrets={"vault_password": VAULT_PW}))
    assert VAULT_PW not in r.text
