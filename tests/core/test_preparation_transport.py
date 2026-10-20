import asyncio
import json
from importlib.util import find_spec

import pytest

from tests.core.test_physical_nodes import proof


def transport():
    assert find_spec("app.core.preparation_transport") is not None, (
        "Missing bounded optional preparation transport"
    )
    from app.core import preparation_transport

    return preparation_transport


def config(tmp_path, monkeypatch, **changes):
    ssh = tmp_path / "ssh_config"
    ssh.write_text("Host range42-preparation-test\n  HostName 192.0.2.10\n")
    ssh.chmod(0o600)
    value = {
        "version": 1,
        "installation_id": "c" * 32,
        "ssh_config": str(ssh),
        "agents": [
            {
                "target_host_id": "first",
                "node_name": "pve-test",
                "ssh_alias": "range42-preparation-test",
            }
        ],
        **changes,
    }
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("RANGE42_PREPARATION_AGENTS_FILE", str(path))
    return path


def test_missing_capability_is_unavailable_and_private_configuration_cannot_supply_commands(
    tmp_path, monkeypatch
):
    mod = transport()
    from app.core.errors import Range42Error

    monkeypatch.delenv("RANGE42_PREPARATION_AGENTS_FILE", raising=False)
    with pytest.raises(Range42Error) as error:
        mod.agent_configuration("first", "pve-test")
    assert error.value.code == "PREPARATION_AGENT_UNAVAILABLE"
    path = config(tmp_path, monkeypatch, command="/bin/sh")
    with pytest.raises(Range42Error):
        mod.agent_configuration("first", "pve-test")
    path = config(tmp_path, monkeypatch)
    expected = mod.agent_configuration("first", "pve-test")
    assert expected.alias == "range42-preparation-test"
    for host, node in [("other", "pve-test"), ("first", "other")]:
        with pytest.raises(Range42Error):
            mod.agent_configuration(host, node)
    path.chmod(0o644)
    with pytest.raises(Range42Error):
        mod.agent_configuration("first", "pve-test")


class Process:
    def __init__(self, raw):
        self.stdin = self
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(raw)
        self.stdout.feed_eof()
        self.returncode = None
        self.input = b""
        self.killed = False

    def write(self, raw):
        self.input += raw

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_only_fixed_inspection_reaches_a_verified_host_key_ssh_alias(
    tmp_path, monkeypatch
):
    mod = transport()
    config(tmp_path, monkeypatch)
    process = Process(json.dumps({"ok": True, "result": proof()}).encode())
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return process

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", spawn)
    selected = mod.agent_configuration("first", "pve-test")
    observed = await mod.inspect_agent(selected)
    assert observed.physical_node_id == "a" * 32
    assert calls[0][-1] == "range42-preparation-test"
    assert "StrictHostKeyChecking=yes" in calls[0]
    assert json.loads(process.input) == {"action": "inspect"}
    assert not any(value in calls[0] for value in ["sh", "-c", "--initialize"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["oversize", "wrong-installation", "wrong-node", "readiness", "secret-error"],
)
async def test_invalid_or_unbounded_agent_responses_are_refused_without_private_diagnostics(
    tmp_path, monkeypatch, kind
):
    mod = transport()
    config(tmp_path, monkeypatch)
    value = proof()
    if kind == "wrong-installation":
        value["installation_id"] = "f" * 32
    elif kind == "wrong-node":
        value["node_name"] = "other"
    elif kind == "readiness":
        value["execution_ready"] = True
    raw = json.dumps({"ok": True, "result": value}).encode()
    if kind == "oversize":
        raw = b"x" * 16385
    elif kind == "secret-error":
        raw = json.dumps({"ok": False, "error": "PRIVATE-DO-NOT-ECHO"}).encode()
    process = Process(raw)

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", spawn)
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error) as error:
        await mod.inspect_agent(mod.agent_configuration("first", "pve-test"))
    assert "PRIVATE-DO-NOT-ECHO" not in str(error.value)
    assert error.value.code == "PREPARATION_AGENT_UNAVAILABLE"
    if kind == "oversize":
        assert process.killed


def test_https_peer_certificate_must_match_the_root_agent_before_recording_any_identity():
    mod = transport()
    assert hasattr(mod, "peer_certificate_digest"), (
        "HTTPS endpoint proof must come from its actual TLS peer"
    )

    class TLS:
        def getpeercert(self, binary_form=False):
            assert binary_form
            return b"synthetic-peer-der"

    class Stream:
        def get_extra_info(self, key):
            assert key == "ssl_object"
            return TLS()

    import hashlib
    import httpx

    assert (
        mod.peer_certificate_digest(
            httpx.Response(200, extensions={"network_stream": Stream()})
        )
        == hashlib.sha256(b"synthetic-peer-der").hexdigest()
    )
    with pytest.raises(ValueError):
        mod.peer_certificate_digest(httpx.Response(200))
