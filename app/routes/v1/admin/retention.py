"""Snapshot retention settings.

The UI surfaces these in Settings; the backend is authoritative. Stored as a
simple JSON file at ``<workspace_root>/retention.json`` — single source of
truth, rewritten atomically via tmp+rename. A future migration can fold this
into SQLite when the ``app_settings`` table lands, but for v1 the file model
is enough and keeps the concern off the critical deploy path.

Schema (spec §12 snapshot retention, flagged to move to backend):
    {
      "keep_count": int >= 0,  # keep at least this many most-recent snapshots
      "keep_days":  int >= 0,  # keep any snapshot younger than this (days)
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/retention", tags=["v1 admin retention"])


DEFAULT_RETENTION: dict[str, int] = {"keep_count": 5, "keep_days": 7}


class RetentionPolicy(BaseModel):
    """Snapshot retention policy — enforced by the snapshot pipeline."""

    keep_count: Annotated[int, Field(ge=0, description="Keep at least N most-recent snapshots")] = 5
    keep_days: Annotated[int, Field(ge=0, description="Keep snapshots younger than D days")] = 7


def _retention_path() -> Path:
    # Lazy import so pytest's reload(cfg) picks up the current workspace_root.
    from app.core.config import settings
    return Path(settings.workspace_root) / "retention.json"


def _read() -> dict[str, int]:
    p = _retention_path()
    if not p.exists():
        return dict(DEFAULT_RETENTION)
    try:
        data = json.loads(p.read_text())
        return {
            "keep_count": int(data.get("keep_count", DEFAULT_RETENTION["keep_count"])),
            "keep_days": int(data.get("keep_days", DEFAULT_RETENTION["keep_days"])),
        }
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("retention.read_failed", error=str(exc), path=str(p))
        return dict(DEFAULT_RETENTION)


def _write(policy: dict[str, int]) -> None:
    """Atomic write — tmp file + rename."""
    p = _retention_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=".retention.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(policy, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, p)
    except Exception:
        # Best-effort cleanup of the temp file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@router.get("", response_model=RetentionPolicy)
async def get_retention() -> RetentionPolicy:
    return RetentionPolicy(**_read())


@router.put("", response_model=RetentionPolicy)
async def put_retention(policy: RetentionPolicy) -> RetentionPolicy:
    data = policy.model_dump()
    _write(data)
    logger.info("retention.updated", keep_count=data["keep_count"], keep_days=data["keep_days"])
    return RetentionPolicy(**data)
