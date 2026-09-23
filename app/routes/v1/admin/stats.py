"""/v1/admin/stats — in-process SSE + redaction counters."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.subscribers import COUNTERS
from app.schemas.v1.admin import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def stats():
    return StatsResponse(
        open_streams=COUNTERS.open_streams,
        events_emitted_total=COUNTERS.events_emitted_total,
        redactions_total=COUNTERS.redactions_total,
        redactions_by_rule=dict(COUNTERS.redactions_by_rule),
    )
