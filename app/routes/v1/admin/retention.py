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
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, ConfigDict, ValidationError

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/retention", tags=["v1 admin retention"])


DEFAULT_RETENTION: dict[str, int] = {"keep_count": 5, "keep_days": 7}


class RetentionPolicy(BaseModel):
    """Selection policy for explicit reviewed snapshot-set deletion."""

    model_config = ConfigDict(extra="forbid")

    keep_count: Annotated[int, Field(ge=0, le=10000, strict=True, description="Keep at least N most-recent completed sets per deployment")] = 5
    keep_days: Annotated[int, Field(ge=0, le=36500, strict=True, description="Keep completed sets younger than D days")] = 7


class RetentionPolicyStatus(RetentionPolicy):
    automatic_enforcement: Literal[False] = False
    execution: Literal["reviewed_snapshot_sets_only"] = "reviewed_snapshot_sets_only"


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
        return RetentionPolicy.model_validate(data).model_dump()
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("retention.read_failed", error=str(exc), path=str(p))
        return dict(DEFAULT_RETENTION)


def read_for_deletion() -> dict[str, int]:
    """Never select destructive work using a corrupt persisted policy."""
    from app.core.errors import Range42Error
    path = _retention_path()
    try:
        if not path.exists():
            return dict(DEFAULT_RETENTION)
        return RetentionPolicy.model_validate_json(path.read_text()).model_dump()
    except (OSError, ValueError, ValidationError):
        raise Range42Error(status=409, code="RETENTION_POLICY_UNAVAILABLE", error="retention_policy_unavailable",
                           message="Save a valid retention policy before reviewing deletion.") from None


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


@router.get("", response_model=RetentionPolicyStatus)
async def get_retention() -> RetentionPolicyStatus:
    return RetentionPolicyStatus(**_read())


@router.put("", response_model=RetentionPolicyStatus)
async def put_retention(policy: RetentionPolicy) -> RetentionPolicyStatus:
    data = policy.model_dump()
    _write(data)
    logger.info("retention.updated", keep_count=data["keep_count"], keep_days=data["keep_days"])
    return RetentionPolicyStatus(**data)
