"""Authenticated encryption for persisted Git and Proxmox credentials.

The key belongs outside the database and must survive application upgrades.
ORM callers continue to receive the original token only in process memory.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
import re
import stat
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import TypeDecorator

from app.core.config import Settings
from app.core.errors import Range42Error

_PREFIX = "range42:fernet:v1:"
_SECRET_BYTES = 8192


def _reference_error(reason: str) -> Range42Error:
    messages = {
        "DENIED": "Git credential reference is not allowed by the backend operator configuration.",
        "UNAVAILABLE": "Git credential reference is unavailable to the backend process.",
        "INVALID": "Git credential reference must contain one bounded nonempty token in a supported private location.",
        "CHANGED": "Git credential reference changed while being read; retry with a stable credential.",
    }
    return Range42Error(status=503, error="git_credential_reference_error",
                        code="GIT_CREDENTIAL_REFERENCE_" + reason, message=messages[reason])


def _secret_value(value: str) -> str:
    if (not value or len(value.encode("utf-8")) > _SECRET_BYTES or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _reference_error("INVALID")
    return value


def _private(info, *, directory: bool) -> bool:
    return ((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o077
            and (directory or info.st_nlink == 1))


def _file_secret(reference: str) -> str:
    approved = os.getenv("RANGE42_GIT_SECRET_DIR", "")
    if not approved:
        raise _reference_error("DENIED")
    parsed = urlsplit(reference)
    path = Path(unquote(parsed.path, errors="strict"))
    root = Path(approved)
    if (parsed.netloc or parsed.query or parsed.fragment or not path.is_absolute()
            or not root.is_absolute() or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))):
        raise _reference_error("INVALID")
    if path == root or not path.is_relative_to(root):
        raise _reference_error("DENIED")
    descriptors = []
    try:
        # Walk from / using directory descriptors: neither file nor parent
        # symlinks can redirect the approved path into arbitrary server files.
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(parent)
        for index, part in enumerate(path.parts[1:-1], start=1):
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(parent)
            if index >= len(root.parts) - 1 and not _private(os.fstat(parent), directory=True):
                raise _reference_error("INVALID")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not _private(before, directory=False) or before.st_size > _SECRET_BYTES:
            raise _reference_error("INVALID")
        value = os.read(descriptor, _SECRET_BYTES + 1)
        # Some filesystems retain the same timestamps for an immediate
        # same-size rewrite. Check bytes too, on the same owned descriptor.
        os.lseek(descriptor, 0, os.SEEK_SET)
        repeated = os.read(descriptor, _SECRET_BYTES + 1)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(info, field) for field in fields for info in (after, current)):
            raise _reference_error("CHANGED")
        if value != repeated or len(value) != before.st_size:
            raise _reference_error("CHANGED")
        # A normal secret-file trailing LF/CRLF is not part of the token.
        if value.endswith(b"\r\n"):
            value = value[:-2]
        elif value.endswith(b"\n"):
            value = value[:-1]
        return _secret_value(value.decode("utf-8"))
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def resolve_git_credential(value: str | None) -> str | None:
    """Resolve opt-in references once per operation, never during ORM reads.

    Only Git consumers use this resolver. Literal tokens keep their existing
    meaning. Operator allowlists protect other backend environment/files from
    being sent as Git authentication by an API caller. Nothing is cached here.
    """
    if value is None:
        return None
    if value.startswith("env://"):
        name = value[6:]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise _reference_error("INVALID")
        allowed = {item.strip() for item in os.getenv("RANGE42_GIT_SECRET_ENV_ALLOWLIST", "").split(",") if item.strip()}
        if name not in allowed:
            raise _reference_error("DENIED")
        result = os.getenv(name)
        if result is None:
            raise _reference_error("UNAVAILABLE")
        try:
            return _secret_value(result)
        except UnicodeError:
            raise _reference_error("INVALID") from None
    if value.startswith("file://"):
        try:
            return _file_secret(value)
        except UnicodeError:
            raise _reference_error("INVALID") from None
        except (OSError, ValueError):
            # File paths, parser errors and decoded bytes are never exposed.
            raise _reference_error("UNAVAILABLE") from None
    return value


@dataclass(frozen=True)
class GitSource:
    """Operation-local source view; never an ORM row or serialized response."""

    id: str
    provider: str
    base_url: str
    auth_kind: str
    created_at: datetime | None
    reference_sha256: str = field(repr=False)
    token_ref: str | None = field(repr=False)


def resolved_git_source(source) -> GitSource:
    if isinstance(source, GitSource):
        return source
    token = resolve_git_credential(source.token_ref) if source.auth_kind != "none" else None
    return GitSource(source.id, source.provider, source.base_url, source.auth_kind, source.created_at,
                     hashlib.sha256((source.token_ref or "").encode()).hexdigest(), token)


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
