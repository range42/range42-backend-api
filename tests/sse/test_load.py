"""EventsWriter/Reader throughput floor — spec §10 load scenario.

Appends 30k events through EventsWriter (fsync policy intact) and
asserts ≥5000 events/sec write throughput with full-file replay via
EventsReader.read_range() completing in <30s.
"""
import time
import pytest

from app.core.events import EventsWriter, EventsReader


@pytest.mark.asyncio
async def test_writer_handles_5000_eps(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventsWriter(path)
    N = 30_000
    t0 = time.perf_counter()
    for i in range(N):
        writer.append({"event_type": "log_line", "payload": {"i": i}},
                      attempt_id="att-1", deployment_id="dep-1")
    elapsed = time.perf_counter() - t0
    rate = N / elapsed
    assert rate >= 5000, f"writer only {rate:.0f} events/sec"
    read_start = time.perf_counter()
    seen = sum(1 for _ in EventsReader(path).read_range(from_seq=0))
    read_elapsed = time.perf_counter() - read_start
    assert seen == N
    assert read_elapsed < 30
