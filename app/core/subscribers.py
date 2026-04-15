"""In-process subscriber registry + counters.

Single-worker invariant documented in spec §7. Multi-worker scale-out
migrates this to SQLite or Redis — app/routes/v1/admin/stats.py reads
these counters.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Counters:
    open_streams: int = 0
    events_emitted_total: int = 0
    redactions_total: int = 0
    redactions_by_rule: dict[str, int] = field(default_factory=dict)


COUNTERS = Counters()
