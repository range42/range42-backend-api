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


def per_node_config(tmp_path, monkeypatch):
    path = config(tmp_path, monkeypatch)
    value = json.loads(path.read_text())
    value["version"] = 2
    value["agents"][0]["ssh_config"] = value.pop("ssh_config")
    path.write_text(json.dumps(value))
    return path, value


def test_per_node_transport_revision_survives_other_node_enrollment(
    tmp_path, monkeypatch
):
    mod = transport()
    path, value = per_node_config(tmp_path, monkeypatch)
    first = mod.agent_configuration("first", "pve-test")
    other_ssh = tmp_path / "other_ssh"
    other_ssh.write_text("Host range42-preparation-other\n  HostName 192.0.2.11\n")
    other_ssh.chmod(0o600)
    other = {
        "target_host_id": "second",
        "node_name": "pve-second",
        "ssh_alias": "range42-preparation-other",
        "ssh_config": str(other_ssh),
    }
    value["agents"].insert(0, other)
    path.write_text(json.dumps(value))
    assert mod.agent_configuration("first", "pve-test") == first
    second = mod.agent_configuration("second", "pve-second")
    assert second.ssh_config == other_ssh
    other_ssh.write_text(other_ssh.read_text() + "  Port 2222\n")
    assert mod.agent_configuration("first", "pve-test") == first
    assert mod.agent_configuration("second", "pve-second").revision != second.revision
    value["agents"].reverse()
    path.write_text(json.dumps(value))
    assert mod.agent_configuration("first", "pve-test") == first
    value["agents"] = [
        row for row in value["agents"] if row["target_host_id"] == "first"
    ]
    path.write_text(json.dumps(value))
    assert mod.agent_configuration("first", "pve-test") == first


def test_legacy_transport_preserves_original_review_revision(tmp_path, monkeypatch):
    import hashlib

    mod = transport()
    path = config(tmp_path, monkeypatch)
    value = json.loads(path.read_text())
    expected = mod.digest(
        [value, hashlib.sha256((tmp_path / "ssh_config").read_bytes()).hexdigest()]
    )
    assert mod.agent_configuration("first", "pve-test").revision == expected


@pytest.mark.parametrize(
    "change", ["selected-alias", "selected-path", "selected-bytes", "installation"]
)
def test_selected_transport_changes_require_a_new_review(tmp_path, monkeypatch, change):
    mod = transport()
    path, value = per_node_config(tmp_path, monkeypatch)
    first = mod.agent_configuration("first", "pve-test")
    if change == "selected-alias":
        value["agents"][0]["ssh_alias"] = "range42-preparation-replacement"
    elif change == "selected-path":
        new_ssh = tmp_path / "new_ssh"
        new_ssh.write_bytes(first.ssh_config.read_bytes())
        new_ssh.chmod(0o600)
        value["agents"][0]["ssh_config"] = str(new_ssh)
    elif change == "selected-bytes":
        first.ssh_config.write_text(first.ssh_config.read_text() + "  Port 2222\n")
    else:
        value["installation_id"] = "d" * 32
    path.write_text(json.dumps(value))
    assert mod.agent_configuration("first", "pve-test").revision != first.revision


@pytest.mark.parametrize(
    "kind",
    [
        "shared-config",
        "missing-config",
        "public-config",
        "linked-config",
        "relative-config",
        "unknown-command",
        "duplicate-host",
        "boolean-version",
    ],
)
def test_per_node_registry_keeps_the_private_fixed_transport_boundary(
    tmp_path, monkeypatch, kind
):
    from app.core.errors import Range42Error

    mod = transport()
    path, value = per_node_config(tmp_path, monkeypatch)
    row = value["agents"][0]
    if kind == "shared-config":
        value["ssh_config"] = row["ssh_config"]
    elif kind == "missing-config":
        row.pop("ssh_config")
    elif kind == "public-config":
        (tmp_path / "ssh_config").chmod(0o644)
    elif kind == "linked-config":
        link = tmp_path / "linked_ssh"
        link.symlink_to(tmp_path / "ssh_config")
        row["ssh_config"] = str(link)
    elif kind == "relative-config":
        row["ssh_config"] = "ssh_config"
    elif kind == "unknown-command":
        row["command"] = "/bin/sh"
    elif kind == "duplicate-host":
        value["agents"].append(dict(row))
    else:
        value["version"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(Range42Error) as error:
        mod.agent_configuration("first", "pve-test")
    assert error.value.code == "PREPARATION_AGENT_UNAVAILABLE"


@pytest.mark.parametrize("value", [{}, {"agents": []}, [], None, {"version": 2}])
def test_incomplete_transport_registry_has_a_bounded_error(
    tmp_path, monkeypatch, value
):
    from app.core.errors import Range42Error

    path = config(tmp_path, monkeypatch)
    path.write_text(json.dumps(value))
    with pytest.raises(Range42Error) as error:
        transport().agent_configuration("first", "pve-test")
    assert error.value.code == "PREPARATION_AGENT_UNAVAILABLE"
