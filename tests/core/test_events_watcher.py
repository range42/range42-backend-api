import asyncio
import json
import pytest
from app.core.events import EventsWriter, EventsReader
from app.core.redaction import (
    RedactionAuditWriter, ConfigDenylistLayer, VaultTaggedLayer,
    TaintedStringLayer,
)
from app.core.events_watcher import EventsWatcher, _translate


@pytest.mark.parametrize(("message", "code", "detail"), [
    ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! private-diagnostic", "SSH_HOST_KEY_REJECTED", "Verify"),
    ("Host key verification failed. private-diagnostic", "SSH_HOST_KEY_REJECTED", "Verify"),
    ("Permission denied (publickey). private-diagnostic", "SSH_AUTHENTICATION_FAILED", "credentials"),
    ("Connection timed out private-diagnostic", "HOST_UNREACHABLE", "network"),
])
def test_unreachable_events_explain_connection_failures_without_raw_diagnostics(message, code, detail):
    event = _translate({"event": "runner_on_unreachable", "event_data": {
        "host": "owned-guest", "res": {"msg": message},
    }})
    assert event["event_type"] == "host_unreachable"
    assert event["payload"]["host"] == "owned-guest"
    assert event["payload"]["code"] == code
    assert detail in event["payload"]["detail"]
    assert "private-diagnostic" not in json.dumps(event)


def test_unreachable_no_log_result_keeps_only_generic_diagnostic():
    event = _translate({"event": "runner_on_unreachable", "event_data": {
        "host": "owned-guest", "res": {"_ansible_no_log": True, "msg": "Host key verification failed."},
    }})
    assert event["payload"]["code"] == "HOST_UNREACHABLE"


@pytest.mark.asyncio
async def test_restarting_watcher_does_not_replay_persisted_job_events(tmp_path):
    job_events = tmp_path / "job_events"
    job_events.mkdir()
    stop = asyncio.Event()
    stop.set()
    async def drain():
        await EventsWatcher(
            job_events_dir=job_events, writer=EventsWriter(tmp_path / "events.jsonl"),
            audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"), layers=[],
            deployment_id="dep", attempt_id="att", stop=stop,
        ).run()
    (job_events / "1-event.json").write_text(json.dumps({"event": "verbose", "stdout": "first"}))
    await drain()
    (job_events / "2-event.json").write_text(json.dumps({"event": "verbose", "stdout": "second"}))
    await drain()
    events = list(EventsReader(tmp_path / "events.jsonl").read_range())
    assert [event["payload"]["text"] for event in events] == ["first", "second"]


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


@pytest.mark.asyncio
async def test_watcher_drains_final_events_in_counter_order_without_duplicate_lifecycle(tmp_path):
    job_events = tmp_path / "job_events"
    job_events.mkdir()
    for counter, kind in ((10, "runner_on_ok"), (2, "runner_on_start"), (11, "playbook_on_stats")):
        (job_events / f"{counter}-event.json").write_text(json.dumps({
            "counter": counter, "event": kind,
            "event_data": {"task": f"task-{counter}"},
        }))
    stop = asyncio.Event()
    stop.set()  # The process may finish before the watcher's first scheduling turn.
    await EventsWatcher(
        job_events_dir=job_events, writer=EventsWriter(tmp_path / "events.jsonl"),
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"), layers=[],
        deployment_id="dep", attempt_id="attempt", stop=stop,
    ).run()
    events = list(EventsReader(tmp_path / "events.jsonl").read_range(from_seq=0))
    assert [event["payload"].get("task_name") for event in events[:2]] == ["task-2", "task-10"]
    assert not any(event["event_type"] in ("attempt_start", "attempt_end") for event in events)


@pytest.mark.asyncio
async def test_translated_stdout_and_uri_invocation_never_expose_known_credentials(tmp_path):
    job_events = tmp_path / "job_events"
    job_events.mkdir()
    raw_events = [
        {"event": "verbose", "stdout": "request uses known-api-secret"},
        {"event": "runner_on_ok", "event_data": {"task": "Call Proxmox", "host": "proxmox", "res": {
            "invocation": {"module_args": {"headers": {"Authorization": "PVEAPIToken=user!id=known-api-secret"}}},
            "result_values": ["known-guest-password"],
        }}},
    ]
    for index, event in enumerate(raw_events):
        (job_events / f"{index + 1}-event.json").write_text(json.dumps(event))
    stop = asyncio.Event()
    stop.set()
    await EventsWatcher(
        job_events_dir=job_events, writer=EventsWriter(tmp_path / "events.jsonl"),
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"),
        layers=[TaintedStringLayer({"known-api-secret", "known-guest-password"})],
        deployment_id="dep", attempt_id="attempt", stop=stop,
    ).run()
    saved = (tmp_path / "events.jsonl").read_text()
    assert "known-api-secret" not in saved
    assert "known-guest-password" not in saved
    events = list(EventsReader(tmp_path / "events.jsonl").read_range())
    assert events[0]["event_type"] == "log_line"
    assert events[1]["payload"]["result"] == "ok"


@pytest.mark.asyncio
async def test_stdout_is_redacted_before_its_length_limit(tmp_path):
    job_events = tmp_path / "job_events"
    job_events.mkdir()
    secret = "private-credential-crossing-output-limit"
    (job_events / "1-event.json").write_text(json.dumps({
        "event": "verbose", "stdout": "x" * 4090 + secret,
    }))
    stop = asyncio.Event()
    stop.set()
    await EventsWatcher(
        job_events_dir=job_events, writer=EventsWriter(tmp_path / "events.jsonl"),
        audit=RedactionAuditWriter(tmp_path / "redactions.jsonl"),
        layers=[TaintedStringLayer({secret})], deployment_id="dep",
        attempt_id="attempt", stop=stop,
    ).run()

    event = next(EventsReader(tmp_path / "events.jsonl").read_range())
    assert event["payload"]["text"] == ("x" * 4090 + "[REDACTED:tainted_string]")[:4096]
