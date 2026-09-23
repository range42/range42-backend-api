import asyncio
import pytest
from app.core.events import EventsWriter, EventsReader, tail_events


def test_reader_reads_from_cursor(tmp_path):
    w = EventsWriter(tmp_path / "e.jsonl")
    for i in range(5):
        w.append({"event_type": "log_line", "payload": {"i": i}}, attempt_id="a")
    r = EventsReader(tmp_path / "e.jsonl")
    got = list(r.read_range(from_seq=3))
    assert [e["event_seq"] for e in got] == [3, 4, 5]


def test_reader_skips_partial_trailing(tmp_path):
    path = tmp_path / "e.jsonl"
    w = EventsWriter(path)
    w.append({"event_type": "log_line"}, attempt_id="a")
    # Simulate a truncated partial line.
    with path.open("ab") as fh:
        fh.write(b'{"event_seq": 99, "partial"')
    r = EventsReader(path)
    got = list(r.read_range(from_seq=0))
    assert len(got) == 1


@pytest.mark.asyncio
async def test_tail_events_delivers_new_lines(tmp_path):
    path = tmp_path / "e.jsonl"
    w = EventsWriter(path)
    w.append({"event_type": "log_line", "payload": {"i": 1}}, attempt_id="a")

    seen = []
    stop = asyncio.Event()

    async def consumer():
        async for ev in tail_events(path, from_seq=0, stop=stop, poll_ms=50):
            seen.append(ev["event_seq"])
            if len(seen) >= 3:
                stop.set()
                return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)
    w.append({"event_type": "log_line"}, attempt_id="a")
    w.append({"event_type": "log_line"}, attempt_id="a")
    await asyncio.wait_for(task, timeout=3)
    assert seen == [1, 2, 3]
