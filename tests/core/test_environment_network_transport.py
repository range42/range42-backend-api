"""The optional network service cannot invalidate the physical agent binding."""

import importlib
import importlib.util
import json

import pytest

from app.core.errors import Range42Error
from tests.core.test_preparation_transport import config, Process


def service():
    assert importlib.util.find_spec("app.core.environment_network_transport"), (
        "Missing separate fixed environment network transport"
    )
    return importlib.import_module("app.core.environment_network_transport")


def network_config(tmp_path, monkeypatch):
    ssh = tmp_path / "network-ssh"
    ssh.write_text("Host range42-env-network-test\n  HostName 192.0.2.10\n")
    ssh.chmod(0o600)
    value = {
        "version": 2,
        "installation_id": "c" * 32,
        "agents": [
            {
                "target_host_id": "first",
                "node_name": "pve-test",
                "ssh_alias": "range42-env-network-test",
                "ssh_config": str(ssh),
            }
        ],
    }
    path = tmp_path / "network-agents.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("RANGE42_ENVIRONMENT_NETWORK_AGENTS_FILE", str(path))
    return path


def test_network_enrollment_is_separate_and_preserves_original_transport_revision(
    tmp_path, monkeypatch
):
    from app.core import preparation_transport

    config(tmp_path, monkeypatch)
    before = preparation_transport.agent_configuration("first", "pve-test")
    network_config(tmp_path, monkeypatch)
    selected = service().configuration("first", "pve-test")
    assert selected.alias == "range42-env-network-test"
    assert preparation_transport.agent_configuration("first", "pve-test") == before
    assert selected.revision != before.revision
    monkeypatch.delenv("RANGE42_ENVIRONMENT_NETWORK_AGENTS_FILE")
    with pytest.raises(Range42Error) as error:
        service().configuration("first", "pve-test")
    assert error.value.code == "ENVIRONMENT_NETWORK_UNAVAILABLE"
    assert preparation_transport.agent_configuration("first", "pve-test") == before


@pytest.mark.asyncio
async def test_network_transport_uses_only_fixed_actions_and_verified_alias(
    tmp_path, monkeypatch
):
    from app.core import preparation_transport

    network_config(tmp_path, monkeypatch)
    selected = service().configuration("first", "pve-test")
    process = Process(json.dumps({"ok": True, "result": {"synthetic": True}}).encode())
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return process

    monkeypatch.setattr(preparation_transport.asyncio, "create_subprocess_exec", spawn)
    assert await service().read(
        selected, action="prepare", request={"original": True}
    ) == {"synthetic": True}
    assert calls[0][-1] == "range42-env-network-test"
    assert "StrictHostKeyChecking=yes" in calls[0]
    assert json.loads(process.input) == {
        "action": "prepare",
        "request": {"original": True},
    }
    for action in [
        "shell",
        "--initialize",
        "inspect",
        "capacity-admit",
        "--ensure-guard",
    ]:
        with pytest.raises(Range42Error):
            await service().read(selected, action=action, request={})
    assert len(calls) == 1


def test_network_option_is_explicit_foundation_only_and_preserves_existing_native_shape():
    from app.schemas.v1.deployments import NativeDeployment

    value = {"context_id": "example", "path": "scenarios/foundation"}
    assert (
        "prepare_environment_networks"
        not in NativeDeployment.model_validate(value).model_dump()
    )
    foundation = {
        "namespace": "workshop",
        "source_node": "source",
        "proxmox_source": 9230,
        "deployer_source": 9222,
    }
    assert (
        NativeDeployment.model_validate(
            {**value, "foundation": foundation, "prepare_environment_networks": True}
        ).model_dump()["prepare_environment_networks"]
        is True
    )
    for invalid in [
        {**value, "prepare_environment_networks": True},
        {**value, "foundation": foundation, "prepare_environment_networks": 1},
    ]:
        with pytest.raises(ValueError):
            NativeDeployment.model_validate(invalid)
