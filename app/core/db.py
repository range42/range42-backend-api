"""SQLAlchemy 2.x async engine + session factory.

WAL + foreign_keys + NORMAL sync enabled via the 'connect' event so every
new aiosqlite connection gets the PRAGMA before the app touches it.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _apply_pragmas(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def build_engine(url: str | None = None) -> AsyncEngine:
    resolved = url or settings.db_url
    # Ensure parent dir exists for file-based sqlite URLs.
    if resolved.startswith("sqlite+aiosqlite:///"):
        db_path = Path(resolved.replace("sqlite+aiosqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(resolved, echo=False, future=True)
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


_engine: AsyncEngine | None = None
_Session: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _Session
    if _Session is None:
        _Session = session_factory(get_engine())
    return _Session


async def dispose_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _Session = None
