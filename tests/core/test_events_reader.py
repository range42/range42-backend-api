import asyncio
import json
import os
import sys
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


@pytest.mark.asyncio
@pytest.mark.parametrize("polling", [False, True])
async def test_idle_tail_does_not_reparse_consumed_events(tmp_path, monkeypatch, polling):
    if polling:
        monkeypatch.setitem(sys.modules, "watchfiles", None)
    path = tmp_path / "events.jsonl"
    writer = EventsWriter(path)
    for _ in range(3):
        writer.append({"event_type": "log_line"}, attempt_id="a")
    parsed = []
    original_loads = json.loads

    def record_loads(value, *args, **kwargs):
        parsed.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", record_loads)
    stop, ready, appended = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []

    async def consume():
        async for event in tail_events(path, from_seq=2, stop=stop, poll_ms=10):
            seen.append(event["event_seq"])
            if event["event_seq"] == 3:
                ready.set()
            if event["event_seq"] == 4:
                appended.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        parsed.clear()
        # Watch notifications from unrelated workspace files also cannot justify
        # reparsing an unchanged event log.
        (tmp_path / "unrelated.txt").write_text("changed")
        await asyncio.sleep(0.08)
        assert parsed == [], "Idle tail reparsed already consumed event history"
        writer.append({"event_type": "log_line"}, attempt_id="a")
        await asyncio.wait_for(appended.wait(), timeout=2)
        assert seen == [2, 3, 4]
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rotation", "truncation", "same_size", "partial"])
async def test_tail_keeps_cursor_across_file_changes(tmp_path, change):
    path = tmp_path / "events.jsonl"
    first = b'{"event_seq":1,"payload":{"text":"[REDACTED]"}}\n'
    second = first.replace(b'"event_seq":1', b'"event_seq":2')
    path.write_bytes(first)
    stop = asyncio.Event()
    stream = tail_events(path, from_seq=1, stop=stop, poll_ms=10)
    try:
        assert (await anext(stream))["event_seq"] == 1
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.04)
        original = path.stat()
        if change == "rotation":
            replacement = tmp_path / "replacement.jsonl"
            replacement.write_bytes(second)
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            replacement.replace(path)
        elif change == "truncation":
            path.write_bytes(b"")
            await asyncio.sleep(0.04)
            path.write_bytes(first + second)
        elif change == "same_size":
            path.write_bytes(second)
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        else:
            with path.open("ab") as output:
                output.write(second[:20])
            await asyncio.sleep(0.04)
            assert not pending.done()
            with path.open("ab") as output:
                output.write(second[20:])
        event = await asyncio.wait_for(pending, timeout=2)
        assert event == {"event_seq": 2, "payload": {"text": "[REDACTED]"}}
    finally:
        stop.set()
        await stream.aclose()


@pytest.mark.asyncio
async def test_idle_tail_cancellation_finishes_promptly(tmp_path):
    path = tmp_path / "events.jsonl"
    path.touch()
    stream = tail_events(path, poll_ms=10)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.04)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, timeout=2)
    await stream.aclose()


@pytest.mark.asyncio
async def test_append_during_tail_scan_is_not_hidden_by_idle_cache(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event_seq":1}\n')
    original_read = EventsReader.read_range
    appended = False

    def append_after_read(reader, **kwargs):
        nonlocal appended
        yield from original_read(reader, **kwargs)
        if not appended:
            appended = True
            with path.open("ab") as output:
                output.write(b'{"event_seq":2}\n')

    monkeypatch.setattr(EventsReader, "read_range", append_after_read)
    stream = tail_events(path, poll_ms=10)
    try:
        assert (await anext(stream))["event_seq"] == 1
        assert (await asyncio.wait_for(anext(stream), timeout=2))["event_seq"] == 2
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_tail_does_not_start_a_detached_filesystem_watcher(tmp_path, monkeypatch):
    import watchfiles

    entered = asyncio.Event()

    async def unexpected_watch(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield set()

    monkeypatch.setattr(watchfiles, "awatch", unexpected_watch)
    path = tmp_path / "events.jsonl"
    path.touch()
    stream = tail_events(path, poll_ms=10)
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0.05)
        assert not entered.is_set(), "Event tail started a detached filesystem watcher"
        assert not pending.done()
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()


@pytest.mark.asyncio
async def test_sse_disconnect_leaves_no_tail_or_watcher_task(tmp_path, monkeypatch):
    from sse_starlette.sse import AppStatus, EventSourceResponse

    # sse-starlette keeps its exit event globally; isolate this test's loop.
    monkeypatch.setattr(AppStatus, "should_exit_event", None)

    path = tmp_path / "events.jsonl"
    EventsWriter(path).append({"event_type": "log_line"}, attempt_id="a")
    disconnected = asyncio.Event()
    started = asyncio.Event()
    closed = asyncio.Event()
    previous_tasks = asyncio.all_tasks()

    async def body():
        try:
            async for event in tail_events(path, poll_ms=10):
                yield {"data": str(event["event_seq"])}
        finally:
            closed.set()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            started.set()

    response = asyncio.create_task(EventSourceResponse(body())(
        {"type": "http"}, receive, send,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.04)
        disconnected.set()
        await asyncio.wait_for(response, timeout=2)
        await asyncio.wait_for(closed.wait(), timeout=2)
        assert asyncio.all_tasks() <= previous_tasks
    finally:
        response.cancel()
        await asyncio.gather(response, return_exceptions=True)
