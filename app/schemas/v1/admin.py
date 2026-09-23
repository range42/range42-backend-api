"""Admin endpoint DTOs (stats, etc.)."""
from __future__ import annotations

from pydantic import BaseModel


class StatsResponse(BaseModel):
    open_streams: int
    events_emitted_total: int
    redactions_total: int
    redactions_by_rule: dict[str, int]
