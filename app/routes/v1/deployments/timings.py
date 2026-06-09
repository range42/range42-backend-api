"""/v1/deployments/:id/timings — per-stage, per-team durations from events.jsonl."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.events import EventsReader
from app.core.models import Deployment
from app.schemas.v1.deployments import TimingsResponse, TimingsRow

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@router.get("/{deployment_id}/timings", response_model=TimingsResponse)
async def timings(deployment_id: str,
                  session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(error="not_found", code="NOT_FOUND", status=404,
                           message=f"Deployment {deployment_id} not found")
    events_path = Path(dep.workspace_path) / "events.jsonl"
    open_stages: dict[tuple[int | None, str], datetime] = {}
    rows: list[TimingsRow] = []
    for ev in EventsReader(events_path).read_range(from_seq=0):
        if ev.get("event_type") != "phase_transition":
            continue
        team = ev.get("team_id")
        payload = ev.get("payload") or {}
        to_stage = payload.get("to")
        from_stage = payload.get("from")
        ts = _parse(ev["ts"])
        key_from = (team, from_stage) if from_stage else None
        if key_from and key_from in open_stages:
            start = open_stages.pop(key_from)
            rows.append(TimingsRow(
                stage=from_stage, team_id=team,
                duration_ms=int((ts - start).total_seconds() * 1000),
                start_ts=start, end_ts=ts,
            ))
        if to_stage:
            open_stages[(team, to_stage)] = ts
    return TimingsResponse(deployment_id=deployment_id, rows=rows)
