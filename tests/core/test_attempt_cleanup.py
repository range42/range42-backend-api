"""Private runtime resources survive observation loss and are safely reclaimed."""
import json
import os
from pathlib import Path
import subprocess
import sys

from app.core.runner_detached import _process_identity, record_process_identity
from app.core.scenario_runtime import prepare_runtime_vault
from app.core.ssh_agent import _start_agent
from app.core.events import EventsWriter


def _raw_attempt(tmp_path):
    artifact = tmp_path / "runner" / "raw-attempt"
    (artifact / "job_events").mkdir(parents=True)
    (artifact / "rc").write_text("0")
    (artifact / "status").write_text("successful")
    (artifact / "stdout").write_text("private-raw-output")
    event = artifact / "job_events" / "1-event.json"
    event.write_text('{"stdout":"private-raw-output"}')
    writer = EventsWriter(tmp_path / "events.jsonl")
    writer.append({"event_type": "log_line", "runner_event_id": event.name,
                   "payload": {"text": "[REDACTED]"}}, attempt_id=artifact.name, deployment_id="dep")
    return artifact


def test_terminal_cleanup_removes_raw_output_after_canonical_event_preservation(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = _raw_attempt(tmp_path)
    canonical = (tmp_path / "events.jsonl").read_bytes()
    cleanup_attempt_credentials(tmp_path, artifact)
    assert not (artifact / "job_events/1-event.json").exists()
    assert not (artifact / "stdout").exists()
    assert (artifact / "rc").read_text() == "0"
    assert (artifact / "status").read_text() == "successful"
    assert (tmp_path / "events.jsonl").read_bytes() == canonical
    cleanup_attempt_credentials(tmp_path, artifact)


def test_cleanup_retains_unprocessed_events_and_stdout_for_private_review(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = _raw_attempt(tmp_path)
    pending = artifact / "job_events/2-pending.json"
    pending.write_text('{"stdout":"not yet processed"}')
    cleanup_attempt_credentials(tmp_path, artifact)
    assert not (artifact / "job_events/1-event.json").exists()
    assert pending.read_text() == '{"stdout":"not yet processed"}'
    assert (artifact / "stdout").read_text() == "private-raw-output"


def test_cleanup_does_not_accept_another_attempts_canonical_receipt(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = _raw_attempt(tmp_path)
    log = tmp_path / "events.jsonl"
    log.write_text(log.read_text().replace('"raw-attempt"', '"other-attempt"'))
    cleanup_attempt_credentials(tmp_path, artifact)
    assert (artifact / "job_events/1-event.json").exists()
    assert (artifact / "stdout").exists()


def test_cleanup_preserves_linked_raw_output_targets(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = _raw_attempt(tmp_path)
    outside = tmp_path / "operator-file"
    outside.write_text("keep operator data")
    for path in (artifact / "job_events/1-event.json", artifact / "stdout"):
        path.unlink()
        path.symlink_to(outside)
    cleanup_attempt_credentials(tmp_path, artifact)
    assert outside.read_text() == "keep operator data"


def test_cleanup_preserves_raw_files_without_a_runner_exit(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = _raw_attempt(tmp_path)
    (artifact / "rc").unlink()
    cleanup_attempt_credentials(tmp_path, artifact)
    assert (artifact / "job_events/1-event.json").exists()
    assert (artifact / "stdout").exists()


def test_cleanup_preserves_raw_output_while_verified_runner_is_alive(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_output
    artifact = _raw_attempt(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (artifact / "pid").write_text(str(process.pid))
        assert record_process_identity(artifact, process.pid)
        cleanup_attempt_output(tmp_path, artifact)
        assert (artifact / "job_events/1-event.json").exists()
        assert (artifact / "stdout").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_cleanup_preserves_raw_output_when_canonical_sync_fails(tmp_path, monkeypatch):
    from app.core.attempt_cleanup import cleanup_attempt_output
    artifact = _raw_attempt(tmp_path)
    def cannot_sync(_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(os, "fsync", cannot_sync)
    cleanup_attempt_output(tmp_path, artifact)
    assert (artifact / "job_events/1-event.json").exists()
    assert (artifact / "stdout").exists()


def test_cleanup_rejects_linked_job_event_directories(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_output
    artifact = _raw_attempt(tmp_path)
    outside = tmp_path / "operator-events"
    (artifact / "job_events").rename(outside)
    (artifact / "job_events").symlink_to(outside, target_is_directory=True)
    cleanup_attempt_output(tmp_path, artifact)
    assert (outside / "1-event.json").read_text() == '{"stdout":"private-raw-output"}'
    assert (artifact / "stdout").exists()


def test_ssh_agent_close_rejects_recycled_pid_identity():
    agent = _start_agent()
    identity = dict(_process_identity(agent.pid))
    try:
        agent.identity = {**identity, "start_time": "0"}
        agent.close()
        assert _process_identity(agent.pid) == identity
    finally:
        agent.identity = identity
        agent._closed = False
        agent.close()


def test_private_cleanup_metadata_reaps_agent_and_owned_vault(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials, record_attempt_cleanup
    artifact = tmp_path / "runner/attempt"
    artifact.mkdir(parents=True)
    agent = _start_agent(tmp_path)
    socket_directory = Path(agent.env["SSH_AUTH_SOCK"]).parent
    vault = prepare_runtime_vault(tmp_path)
    try:
        record_attempt_cleanup(artifact, ssh_agent=agent, runtime_vault=vault)
        metadata = artifact / "cleanup.json"
        assert metadata.stat().st_mode & 0o777 == 0o600
        document = json.loads(metadata.read_text())
        assert document["ssh_agent"] == _process_identity(agent.pid)
        assert "SSH_AUTH_SOCK" not in metadata.read_text()
        cleanup_attempt_credentials(tmp_path, artifact)
        assert _process_identity(agent.pid) is None
        assert not vault.path.exists()
        assert not metadata.exists()
        assert not socket_directory.exists()
        cleanup_attempt_credentials(tmp_path, artifact)  # idempotent
    finally:
        agent.close()


def test_recovered_cleanup_preserves_replaced_vault_and_unrelated_process(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials, record_attempt_cleanup
    artifact = tmp_path / "runner/attempt"
    artifact.mkdir(parents=True)
    vault = prepare_runtime_vault(tmp_path)
    record_attempt_cleanup(artifact, ssh_agent=None, runtime_vault=vault)
    replacement = vault.path.with_suffix(".new")
    replacement.write_text("{}\n")
    replacement.replace(vault.path)
    metadata = artifact / "cleanup.json"
    document = json.loads(metadata.read_text())
    document["ssh_agent"] = {**_process_identity(os.getpid()), "start_time": "0"}
    metadata.write_text(json.dumps(document))
    cleanup_attempt_credentials(tmp_path, artifact)
    assert vault.path.read_text() == "{}\n"
    assert _process_identity(os.getpid()) is not None


def test_cleanup_rejects_linked_metadata_without_overwriting_its_target(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = tmp_path / "runner/attempt"
    artifact.mkdir(parents=True)
    target = tmp_path / "unrelated.json"
    target.write_text('{"private": "keep me"}')
    (artifact / "cleanup.json").symlink_to(target)
    cleanup_attempt_credentials(tmp_path, artifact)
    assert target.read_text() == '{"private": "keep me"}'


def test_cleanup_ignores_invalid_process_id_in_corrupt_metadata(tmp_path):
    from app.core.attempt_cleanup import cleanup_attempt_credentials
    artifact = tmp_path / "runner/attempt"
    artifact.mkdir(parents=True)
    (artifact / "cleanup.json").write_text(json.dumps({"version": 1, "ssh_agent": {"pid": 2**128}}))
    cleanup_attempt_credentials(tmp_path, artifact)
    assert not (artifact / "cleanup.json").exists()
