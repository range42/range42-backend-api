"""/v1/deployments/:id/events SSE stream.

Replays historical events from events.jsonl then tails live writes.
Filters team/stage/node applied before emit. Replay cap 5000; explicit
ranges via from_seq+to_seq. Headers: Cache-Control=no-cache,
X-Accel-Buffering=no. sse-starlette 15s heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.events import EventsReader, tail_events
from app.core.models import Deployment
from app.core.subscribers import COUNTERS

router = APIRouter()

REPLAY_CAP = 5000
HEARTBEAT_S = 15


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _filter_event(ev: dict, *, team: int | None, stage: str | None,
                  node: str | None) -> bool:
    if team is not None and ev.get("team_id") != team:
        return False
    if stage is not None and ev.get("stage") != stage:
        return False
    if node is not None and ev.get("node_id") != node:
        return False
    return True


def _snapshot_lines(stream, size: int):
    """Export complete canonical records present at opening, never live-tail."""
    remaining = size
    while remaining > 0:
        line = stream.readline(remaining)
        if not line:
            break
        remaining -= len(line)
        if not line.endswith(b"\n"):
            break
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield line


@router.get("/{deployment_id}/events/download", response_class=StreamingResponse)
async def download_events(deployment_id: str, session: AsyncSession = Depends(_session)):
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise Range42Error(error="not_found", code="NOT_FOUND", status=404, message="Deployment not found")
    path = Path(dep.workspace_path) / "events.jsonl"
    headers = {"Content-Disposition": 'attachment; filename="events.jsonl"', "Cache-Control": "no-store"}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return StreamingResponse(iter(()), media_type="application/x-ndjson", headers=headers)
    except OSError:
        raise Range42Error(error="events_unavailable", code="EVENTS_UNAVAILABLE", status=409, message="The deployment event file is unavailable") from None
    stream = os.fdopen(fd, "rb")
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode):
        stream.close()
        raise Range42Error(error="events_unavailable", code="EVENTS_UNAVAILABLE", status=409, message="The deployment event file is unavailable")
    return StreamingResponse(_snapshot_lines(stream, info.st_size), media_type="application/x-ndjson",
                             headers=headers, background=BackgroundTask(stream.close))


@router.get(
    "/{deployment_id}/events",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Server-sent event stream of deployment events",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def events_stream(deployment_id: str,
                        team: int | None = Query(None),
                        stage: str | None = Query(None),
                        node: str | None = Query(None),
                        from_cursor: int = Query(0, ge=0),
                        from_seq: int | None = Query(None),
                        to_seq: int | None = Query(None),
                        session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    events_path = Path(dep.workspace_path) / "events.jsonl"

    async def event_generator():
        COUNTERS.open_streams += 1
        try:
            # Historical range replay.
            start_seq = from_seq if from_seq is not None else from_cursor
            end_seq = to_seq
            reader = EventsReader(events_path)
            emitted = 0
            last_seq = start_seq - 1 if start_seq > 0 else 0
            for ev in reader.read_range(from_seq=start_seq, to_seq=end_seq):
                if not _filter_event(ev, team=team, stage=stage, node=node):
                    continue
                emitted += 1
                last_seq = ev["event_seq"]
                COUNTERS.events_emitted_total += 1
                yield {
                    "event": ev.get("event_type", "log_line"),
                    "id": str(ev["event_seq"]),
                    "data": json.dumps(ev, separators=(",", ":")),
                }
                if emitted >= REPLAY_CAP:
                    break
            # Live tail (no upper bound given).
            if end_seq is None:
                stop = asyncio.Event()
                async for ev in tail_events(events_path,
                                            from_seq=last_seq + 1,
                                            stop=stop):
                    if not _filter_event(ev, team=team, stage=stage, node=node):
                        continue
                    COUNTERS.events_emitted_total += 1
                    yield {
                        "event": ev.get("event_type", "log_line"),
                        "id": str(ev["event_seq"]),
                        "data": json.dumps(ev, separators=(",", ":")),
                    }
        finally:
            COUNTERS.open_streams = max(0, COUNTERS.open_streams - 1)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return EventSourceResponse(event_generator(), ping=HEARTBEAT_S, headers=headers)
