"""Database credentials must be encrypted while ORM callers receive plaintext."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.core.models import Base, ProxmoxHost, Source


@pytest.mark.asyncio
async def test_source_and_host_credentials_are_not_stored_in_plaintext(tmp_path, monkeypatch):
    from app.core.db import build_engine, session_factory
    monkeypatch.setenv("RANGE42_CREDENTIAL_KEY", Fernet.generate_key().decode())
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'encrypted.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory(engine)() as session:
            session.add(Source(id="source", provider="github", base_url="https://github.com", auth_kind="pat", token_ref="source-secret"))
            session.add(ProxmoxHost(id="host", name="pve", api_url="https://pve:8006", node_name="pve", token_ref="host-secret"))
            await session.commit()
        async with engine.connect() as conn:
            stored_source = (await conn.execute(text("SELECT token_ref FROM sources"))).scalar_one()
            stored_host = (await conn.execute(text("SELECT token_ref FROM proxmox_hosts"))).scalar_one()
        assert stored_source != "source-secret"
        assert stored_host != "host-secret"
        async with session_factory(engine)() as session:
            assert (await session.execute(select(Source.token_ref))).scalar_one() == "source-secret"
            assert (await session.execute(select(ProxmoxHost.token_ref))).scalar_one() == "host-secret"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_migrates_existing_plaintext_credentials_once(tmp_path, monkeypatch):
    from app.core import credential_store
    from app.core.db import build_engine
    monkeypatch.setenv("RANGE42_CREDENTIAL_KEY", Fernet.generate_key().decode())
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("INSERT INTO sources (id, provider, base_url, auth_kind, token_ref, created_at) VALUES ('legacy', 'github', 'https://github.com', 'pat', 'old-secret', CURRENT_TIMESTAMP)"))
        assert await credential_store.encrypt_legacy_credentials(engine) == 1
        assert await credential_store.encrypt_legacy_credentials(engine) == 0
        async with engine.connect() as conn:
            stored = (await conn.execute(text("SELECT token_ref FROM sources"))).scalar_one()
        assert "old-secret" not in stored
        assert credential_store.decrypt_credential(stored) == "old-secret"
    finally:
        await engine.dispose()


def test_missing_key_is_rejected_without_disclosing_credential(monkeypatch):
    from app.core import credential_store
    monkeypatch.delenv("RANGE42_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("RANGE42_CREDENTIAL_KEY_FILE", raising=False)
    with pytest.raises(RuntimeError, match="RANGE42_CREDENTIAL_KEY") as exc:
        credential_store.encrypt_credential("a-secret-never-in-the-error")
    assert "a-secret-never" not in str(exc.value)


def test_wrong_key_fails_instead_of_returning_ciphertext_as_password(monkeypatch):
    from app.core import credential_store
    monkeypatch.setenv("RANGE42_CREDENTIAL_KEY", Fernet.generate_key().decode())
    encrypted = credential_store.encrypt_credential("source-secret")
    monkeypatch.setenv("RANGE42_CREDENTIAL_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="Cannot decrypt stored credential"):
        credential_store.decrypt_credential(encrypted)
