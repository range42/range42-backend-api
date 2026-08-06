import asyncio
import json
import pytest
from app.core.events import EventsWriter, EventsReader
from app.core.redaction import (
    RedactionAuditWriter, ConfigDenylistLayer, VaultTaggedLayer,
)
from app.core.events_watcher import EventsWatcher


@pytest.mark.asyncio
async def test_watcher_translates_and_redacts(tmp_path):
    job_events = tmp_path / "art" / "job_events"
    job_events.mkdir(parents=True)
    writer = EventsWriter(tmp_path / "events.jsonl")
    audit = RedactionAuditWriter(tmp_path / "redactions.jsonl")
    layers = [ConfigDenylistLayer(("*_password",)), VaultTaggedLayer()]
    stop = asyncio.Event()

    watcher = EventsWatcher(
        job_events_dir=job_events, writer=writer, audit=audit,
        layers=layers, deployment_id="dep-1", attempt_id="att-1",
        stop=stop, poll_ms=50,
    )
    task = asyncio.create_task(watcher.run())

    # Emit a synthetic ansible-runner event file.
    (job_events / "1-event.json").write_text(json.dumps({
        "event": "runner_on_ok",
        "event_data": {
            "task": "set admin_password",
            "res": {"ansible_password": "secret"},
            "host": "h1",
        },
        "created": "2026-04-14T13:47:00.000Z",
    }))
    await asyncio.sleep(0.3)
    stop.set()
    await task

    lines = list(EventsReader(tmp_path / "events.jsonl").read_range(from_seq=0))
    assert len(lines) >= 1
    ev = lines[-1]
    assert ev["event_type"] in ("task_end", "log_line")
    body = json.dumps(ev)
    assert "secret" not in body
    assert (tmp_path / "redactions.jsonl").read_text().strip() != ""
