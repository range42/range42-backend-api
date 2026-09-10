"""Watcher progress is batched and canonical events retain their classification."""
import asyncio
import json

import pytest

from app.core.events import EventsReader, EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.redaction import RedactionAuditWriter


@pytest.mark.asyncio
async def test_log_lines_retain_ansible_event_and_task_action(tmp_path):
    events = tmp_path / "job_events"
    events.mkdir()
    (events / "1-event.json").write_text(json.dumps({
        "event": "runner_on_async_poll", "stdout": "still running",
        "event_data": {"task_action": "ansible.builtin.command"},
    }))
    stop = asyncio.Event()
    stop.set()
    await EventsWatcher(
        job_events_dir=events, writer=EventsWriter(tmp_path / "events.jsonl"),
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"), layers=[],
        deployment_id="dep", attempt_id="att", stop=stop,
    ).run()
    saved = next(EventsReader(tmp_path / "events.jsonl").read_range())
    assert saved["event_type"] == "log_line"
    assert saved["payload"] == {
        "text": "still running", "ansible_event": "runner_on_async_poll",
        "task_action": "ansible.builtin.command",
    }


@pytest.mark.asyncio
async def test_one_progress_update_per_batch_and_no_repeated_idle_updates(tmp_path):
    events = tmp_path / "job_events"
    events.mkdir()
    for counter in range(1, 21):
        (events / f"{counter}-event.json").write_text(json.dumps({
            "event": "verbose", "stdout": str(counter),
        }))
    calls = []
    changed = asyncio.Event()

    async def progress(cursor):
        calls.append(cursor)
        changed.set()

    stop = asyncio.Event()
    task = asyncio.create_task(EventsWatcher(
        job_events_dir=events, writer=EventsWriter(tmp_path / "events.jsonl"),
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"), layers=[],
        deployment_id="dep", attempt_id="att", stop=stop, poll_ms=10,
        on_progress=progress,
    ).run())
    try:
        await asyncio.wait_for(changed.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert calls == [20]
    finally:
        stop.set()
        await task
    assert calls == [20]


@pytest.mark.asyncio
async def test_recovered_progress_uses_only_events_belonging_to_attempt(tmp_path):
    writer = EventsWriter(tmp_path / "events.jsonl")
    writer.append({"event_type": "attempt_start"}, attempt_id="att")
    writer.append({"event_type": "attempt_start"}, attempt_id="different")
    calls = []

    async def progress(cursor):
        calls.append(cursor)

    stop = asyncio.Event()
    stop.set()
    await EventsWatcher(
        job_events_dir=tmp_path / "job_events", writer=writer,
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"), layers=[],
        deployment_id="dep", attempt_id="att", stop=stop, on_progress=progress,
    ).run()
    assert calls == [1]
