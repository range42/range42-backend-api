import pytest
from sqlalchemy import text
from app.core.db import build_engine, session_factory


@pytest.mark.asyncio
async def test_engine_applies_wal_pragma(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = build_engine(url)
    async with engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
        sync = (await conn.execute(text("PRAGMA synchronous"))).scalar()
    await engine.dispose()
    assert mode.lower() == "wal"
    assert fk == 1
    assert sync == 1  # NORMAL


@pytest.mark.asyncio
async def test_session_factory_yields_working_session(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = build_engine(url)
    Session = session_factory(engine)
    async with Session() as s:
        r = await s.execute(text("SELECT 1"))
        assert r.scalar() == 1
    await engine.dispose()
