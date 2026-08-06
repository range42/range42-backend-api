"""Creating a deployment seeds <ws>/secrets/vault_pass.txt from the payload.

`deploy_trigger` reads that file to set ANSIBLE_VAULT_PASSWORD_FILE and to
unlock the workspace SSH keys (#112). Nothing else wrote it, so a deployment
created through the API could not decrypt anything — the UI collects the
password on the deploy form and it had nowhere to go.
"""
import pathlib
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


@pytest.mark.asyncio
async def test_duplicate_codename_is_rejected_without_touching_the_workspace(
    tmp_path, monkeypatch,
):
    """A second deploy with the same codename must not clobber the first.

    (codename, scenario_label) is unique AND names the workspace directory,
    so Workspace.create reuses the existing one. Writing the vault password
    before the commit meant the duplicate overwrote a live deployment's
    password, then failed with an opaque 500 — leaving the original unable to
    decrypt its vault or unlock its SSH keys.
    """
    app = await _boot(tmp_path, monkeypatch)
    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        first = await c.post("/v1/deployments/",
                             json=_payload(secrets={"vault_password": VAULT_PW}))
        assert first.status_code == 201, first.text
        assert vault_pass.read_text() == VAULT_PW

        second = await c.post("/v1/deployments/",
                              json=_payload(secrets={"vault_password": "OTHER"}))

    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "DEPLOYMENT_EXISTS"
    assert first.json()["id"] in str(body["details"])
    assert vault_pass.read_text() == VAULT_PW, "the live deployment was clobbered"


@pytest.mark.asyncio
async def test_failed_seed_leaves_no_deployment_row(tmp_path, monkeypatch):
    """A write failure must roll the deployment back, not strand it.

    Committing before the seed left a row whose workspace had no password,
    and the obvious retry then hit the 409 instead of fixing itself — the
    deployment was unusable until someone deleted it by hand.
    """
    app = await _boot(tmp_path, monkeypatch)

    real_write = pathlib.Path.write_text

    def _boom(self, *a, **kw):
        if self.name == "vault_pass.txt":
            raise OSError(28, "No space left on device")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", _boom)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        failed = await c.post("/v1/deployments/",
                              json=_payload(secrets={"vault_password": VAULT_PW}))
        assert failed.status_code == 500, failed.text
        assert failed.json()["code"] == "VAULT_SEED_FAILED"

        listed = await c.get("/v1/deployments/")
        assert listed.json()["items"] == [], "the failed create left a row behind"

        # The obvious next move must work rather than 409.
        monkeypatch.setattr(pathlib.Path, "write_text", real_write)
        retry = await c.post("/v1/deployments/",
                             json=_payload(secrets={"vault_password": VAULT_PW}))
        assert retry.status_code == 201, retry.text

    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"
    assert vault_pass.read_text() == VAULT_PW


@pytest.mark.asyncio
async def test_unknown_project_and_host_report_what_is_actually_wrong(
    tmp_path, monkeypatch,
):
    """FKs are enforced at DB level, so a bad reference used to surface from
    the flush as an IntegrityError and get reported as "already exists"."""
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        bad_project = await c.post(
            "/v1/deployments/", json=_payload(codename="BRAVO", project_id="nope"))
        bad_host = await c.post(
            "/v1/deployments/", json=_payload(codename="CHARLIE", target_host_id="nope"))

    for r, missing in ((bad_project, "Project"), (bad_host, "ProxmoxHost")):
        assert r.status_code == 404, r.text
        assert r.json()["code"] == "NOT_FOUND"
        assert missing in r.json()["message"]
        assert "already exists" not in r.json()["message"]


@pytest.mark.asyncio
async def test_commit_failure_does_not_leave_a_stale_secret(tmp_path, monkeypatch):
    """A commit failure after seeding must not leave the password behind.

    The row would not exist, but a later create that supplies no password
    skips the seed branch entirely — and would silently adopt a secret
    belonging to a deployment that never existed.
    """
    app = await _boot(tmp_path, monkeypatch)
    vault_pass = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets" / "vault_pass.txt"

    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit

    async def _boom(self):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(AsyncSession, "commit", _boom)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/deployments/",
                         json=_payload(secrets={"vault_password": VAULT_PW}))
    assert r.status_code == 500
    assert r.json()["code"] == "DEPLOYMENT_PERSIST_FAILED"
    assert not vault_pass.exists(), "a secret was left for a deployment that never existed"

    monkeypatch.setattr(AsyncSession, "commit", real_commit)


@pytest.mark.asyncio
async def test_commit_failure_restores_an_operator_seeded_secret(
    tmp_path, monkeypatch,
):
    """If the workspace already held a password, put it back — do not delete."""
    app = await _boot(tmp_path, monkeypatch)
    ws_secrets = tmp_path / "ws" / "ALPHA-demo_lab" / "secrets"
    ws_secrets.mkdir(parents=True, exist_ok=True)
    vault_pass = ws_secrets / "vault_pass.txt"
    vault_pass.write_text("OPERATOR-SEEDED")

    from sqlalchemy.ext.asyncio import AsyncSession
    real_commit = AsyncSession.commit

    async def _boom(self):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(AsyncSession, "commit", _boom)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        await c.post("/v1/deployments/",
                     json=_payload(secrets={"vault_password": VAULT_PW}))
    assert vault_pass.read_text() == "OPERATOR-SEEDED"

    monkeypatch.setattr(AsyncSession, "commit", real_commit)
