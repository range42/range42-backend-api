import asyncio
import os
import shlex
import subprocess
import sys
import pytest

from app.core.allocation import (
    allocate_vmids,
    allocate_vmids_locked,
    ssh_controlmaster_env,
)


@pytest.mark.asyncio
async def test_allocate_vmids_is_contiguous_and_skips_protected():
    reserved = {100, 101, 4000, 4001}
    out = allocate_vmids(
        start=99, count=5, reserved=reserved, host_overrides=None
    )
    # Must not include any reserved or default-protected vmid.
    for v in out:
        assert v not in reserved
        assert not (100 <= v <= 101)
        assert not (4000 <= v <= 4004)
    assert len(out) == 5
    assert len(set(out)) == 5


@pytest.mark.asyncio
async def test_mutex_serialises_allocations():
    # Two concurrent allocations should not overlap.
    reserved: set[int] = set()

    async def work(start):
        vmids = await allocate_vmids_locked(
            start=start, count=3, reserved=reserved, host_overrides=None
        )
        reserved.update(vmids)
        return vmids

    a, b = await asyncio.gather(work(200), work(200))
    assert set(a).isdisjoint(set(b))


@pytest.mark.asyncio
async def test_host_overrides_are_skipped():
    out = allocate_vmids(
        start=200, count=3, reserved=set(), host_overrides=[[200, 202]]
    )
    for v in out:
        assert v not in {200, 201, 202}
    assert out == [203, 204, 205]


@pytest.mark.asyncio
async def test_exhaustion_raises():
    with pytest.raises(RuntimeError):
        allocate_vmids(
            start=99998, count=5, reserved=set(), host_overrides=None
        )


def test_ssh_controlmaster_env_is_namespaced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env = ssh_controlmaster_env(deployment_id="dep-abc")
    assert "ANSIBLE_SSH_ARGS" in env
    assert "control-dep-abc" in env["ANSIBLE_SSH_ARGS"]
    assert "ControlMaster=auto" in env["ANSIBLE_SSH_ARGS"]


def test_ssh_controlmaster_env_explicit_home(tmp_path):
    env = ssh_controlmaster_env(deployment_id="dep-xyz", home=tmp_path)
    # Directory must be created.
    assert (tmp_path / ".ssh" / "range42").is_dir()
    assert str(tmp_path) in env["ANSIBLE_SSH_ARGS"]
    assert "control-dep-xyz" in env["ANSIBLE_SSH_ARGS"]


@pytest.mark.parametrize("policy", ["accept-new", "yes"])
def test_controlmaster_preserves_inventory_host_key_policy(tmp_path, policy):
    env = ssh_controlmaster_env(deployment_id="dep-keys", home=tmp_path)
    known_hosts = tmp_path / "workspace-known-hosts"
    # The Ansible SSH plugin adds ssh_args before inventory common arguments.
    # Inspect effective OpenSSH behavior, since the first option wins.
    result = subprocess.run([
        "ssh", "-G", "-F", "/dev/null", *shlex.split(env["ANSIBLE_SSH_ARGS"]),
        "-o", f"StrictHostKeyChecking={policy}",
        "-o", f"UserKnownHostsFile={known_hosts}", "guest.invalid",
    ], check=True, capture_output=True, text=True, timeout=5)
    options = dict(line.split(" ", 1) for line in result.stdout.splitlines())
    assert options["stricthostkeychecking"] == ("true" if policy == "yes" else policy)
    assert options["userknownhostsfile"] == str(known_hosts)


def test_controlmaster_quotes_operator_home(tmp_path):
    home = tmp_path / "operator home"
    env = ssh_controlmaster_env(deployment_id="dep-space", home=home)
    result = subprocess.run([
        "ssh", "-G", "-F", "/dev/null", *shlex.split(env["ANSIBLE_SSH_ARGS"]), "guest.invalid",
    ], check=True, capture_output=True, text=True, timeout=5)
    options = dict(line.split(" ", 1) for line in result.stdout.splitlines())
    assert options["hostname"] == "guest.invalid"
    assert options["controlpath"].startswith(str(home / ".ssh" / "range42" / "control-dep-space-"))


def test_runner_keeps_host_key_checks_when_legacy_ansible_config_disables_them(tmp_path):
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nhost_key_checking = False\n")
    generated = ssh_controlmaster_env(deployment_id="dep-legacy", home=tmp_path)
    result = subprocess.run([
        sys.executable, "-c",
        "from ansible.playbook.play_context import PlayContext; "
        "from ansible.plugins.loader import connection_loader; "
        "connection = connection_loader.get('ssh', PlayContext(), None); "
        "connection.set_options(); print(connection.get_option('host_key_checking'))",
    ], env={**os.environ, "ANSIBLE_CONFIG": str(config), **generated},
        check=True, capture_output=True, text=True, timeout=10)
    assert result.stdout.strip() == "True"
