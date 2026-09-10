"""Authenticated encryption for persisted Git and Proxmox credentials.

The key belongs outside the database and must survive application upgrades.
ORM callers continue to receive the original token only in process memory.
"""
from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import TypeDecorator

from app.core.config import Settings

_PREFIX = "range42:fernet:v1:"


def credential_cipher(settings: Settings | None = None) -> Fernet:
    settings = settings or Settings()
    if settings.credential_key and settings.credential_key_file:
        raise RuntimeError("Configure only one of RANGE42_CREDENTIAL_KEY or RANGE42_CREDENTIAL_KEY_FILE")
    key = settings.credential_key
    if settings.credential_key_file:
        try:
            key = Path(settings.credential_key_file).read_text().strip()
        except OSError as exc:
            raise RuntimeError("Cannot read RANGE42_CREDENTIAL_KEY_FILE") from exc
    if not key:
        raise RuntimeError("RANGE42_CREDENTIAL_KEY or RANGE42_CREDENTIAL_KEY_FILE is required to encrypt credentials")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("RANGE42_CREDENTIAL_KEY must be a valid Fernet key") from exc


def encrypt_credential(value: str | None) -> str | None:
    if value is None:
        return None
    return _PREFIX + credential_cipher().encrypt(value.encode()).decode()


def decrypt_credential(value: str | None) -> str | None:
    if value is None or not value.startswith(_PREFIX):
        # Legacy values are converted transactionally before the server starts.
        return value
    try:
        return credential_cipher().decrypt(value[len(_PREFIX):].encode()).decode()
    except (InvalidToken, UnicodeError) as exc:
        raise RuntimeError("Cannot decrypt stored credential; restore the original credential key") from exc


class EncryptedCredential(TypeDecorator):
    """Transparent token encryption on writes and decryption on ORM reads."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_credential(value)

    def process_result_value(self, value, dialect):
        return decrypt_credential(value)


async def encrypt_legacy_credentials(engine: AsyncEngine) -> int:
    """Upgrade legacy token columns atomically; verify existing encrypted rows.

    No schema revision is needed for SQLite's existing VARCHAR columns. Old
    database backups remain sensitive and need separate retention/rotation.
    """
    changed = 0
    async with engine.begin() as connection:
        tables = await connection.run_sync(lambda conn: set(inspect(conn).get_table_names()))
        for table in ("sources", "proxmox_hosts"):
            if table not in tables:
                continue
            rows = (await connection.execute(text(f"SELECT id, token_ref FROM {table} WHERE token_ref IS NOT NULL"))).all()
            for row in rows:
                if row.token_ref.startswith(_PREFIX):
                    decrypt_credential(row.token_ref)
                else:
                    await connection.execute(text(f"UPDATE {table} SET token_ref = :value WHERE id = :id"),
                                             {"value": encrypt_credential(row.token_ref), "id": row.id})
                    changed += 1
    return changed
