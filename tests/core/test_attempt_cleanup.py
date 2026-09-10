"""Private runtime resources survive observation loss and are safely reclaimed."""
import json
import os

from app.core.runner_detached import _process_identity
from app.core.scenario_runtime import prepare_runtime_vault
from app.core.ssh_agent import _start_agent


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
    agent = _start_agent()
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
