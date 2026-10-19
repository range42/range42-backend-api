"""Exercise the real CLI, native API adapter and catalog readiness together."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import threading

import paramiko
import pytest
import yaml

from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario


ROOT = Path(__file__).resolve().parents[2]
CORE = Path(os.getenv("RANGE42_CORE_SOURCE", str(ROOT.parent / "range42")))
CATALOG = Path(os.getenv("RANGE42_CATALOG_SOURCE", str(ROOT.parent / "range42-catalog")))


@contextmanager
def ssh_guest():
    """Serve only a disposable loopback guest; no system sshd or real credentials."""
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.2)
    stop, executed = threading.Event(), threading.Event()

    class Server(paramiko.ServerInterface):
        def check_auth_none(self, username):
            return paramiko.AUTH_SUCCESSFUL

        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            if command != b"true":
                return False
            executed.set()
            return True

    def serve():
        while not stop.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            transport = paramiko.Transport(client)
            try:
                transport.add_server_key(key)
                transport.start_server(server=Server())
                channel = transport.accept(3)
                if channel is not None and executed.wait(3):
                    channel.send_exit_status(0)
                    channel.close()
                    transport.join(timeout=3)
            except (EOFError, OSError, paramiko.SSHException):
                pass
            finally:
                transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], key, executed
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=4)
        assert not thread.is_alive()


@pytest.mark.parametrize("caller,pin,success", [
    ("cli", "empty", True), ("cli", "matching", True),
    ("api", "empty", False), ("api", "matching", True), ("api", "changed", False),
])
def test_native_cli_and_api_guest_trust(tmp_path, monkeypatch, caller, pin, success):
    core_script = CORE / "roles/deployer.bootstrap/files/range42-context.sh"
    readiness = CATALOG / "02_ansible_layer/admin/roles/ansible.utils/tasks/wait/openssh_server/is_reachable.yml"
    if not core_script.is_file() or not readiness.is_file():
        pytest.skip("set RANGE42_CORE_SOURCE and RANGE42_CATALOG_SOURCE to the matching native sources")
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text(core_script.read_text())
    base = scenario(tmp_path / "saved", "training/exercise")
    checkout = tmp_path / "range42"
    for directory in (tmp_path / ".ssh", checkout / "range42", checkout / "range42-ansible_roles-debug-devkit",
                      checkout / "range42-playbooks/scenarios", tmp_path / "bin"):
        directory.mkdir(parents=True, exist_ok=True)
    (checkout / "range42-playbooks/scenarios/demo").symlink_to(base, target_is_directory=True)
    (ws / "scenario").unlink()
    (ws / "scenario").symlink_to(base, target_is_directory=True)
    (base / "manifest/scenario_vms.json").write_text(json.dumps({"version": 2, "vms": [{"vm_id": 2001, "ip": "127.0.0.1"}]}))
    (checkout / "range42/ansible.cfg").write_text("[defaults]\nhost_key_checking=False\nretry_files_enabled=False\n")
    pins = tmp_path / ".ssh/known_hosts"
    ssh_config = tmp_path / ".ssh/config_range42-lab-demo"
    (tmp_path / ".ssh/config").write_text("#### BEGIN RANGE42 INCLUDE\n# Include " + str(ssh_config) + "\n#### END RANGE42 INCLUDE\n")
    (tmp_path / ".zshrc").write_text('#source "' + str(ws / "sourced_range42.sh") + '"\n')
    with (ws / "sourced_range42.sh").open("a") as stream:
        stream.write("export RANGE42_GITDIR__ROOT_DIR=" + shlex.quote(str(checkout)) + "\n"
                     "export RANGE42_CONFIG__ROOT_DIR=" + shlex.quote(str(ws)) + "\n"
                     "export RANGE42_ANSIBLE_ROLES__INVENTORY_DIR=" + shlex.quote(str(ws / "inventory")) + "\n"
                     # A context cannot disable the API's verified connection policy.
                     "export ANSIBLE_HOST_KEY_CHECKING=False\n")
    marker = tmp_path / "configured"
    tasks = yaml.safe_load(readiness.read_text())
    for task in tasks:
        if task.get("name") == "WAIT FOR SSH - VIA ProxyJump":
            # Production task and trust behavior; shorten only retry timers.
            task["retries"], task["delay"] = 0, 0
    tasks.append({"ansible.builtin.copy": {"content": "ready", "dest": str(marker), "mode": "0600"},
                  "delegate_to": "localhost"})
    (base / "main.yml").write_text(yaml.safe_dump([{"hosts": "guest.invalid", "gather_facts": False, "tasks": tasks}]))
    (base / "exercise.setup.sh").write_text('#!/bin/sh\nexec ansible-playbook -i "$RANGE42_ANSIBLE_ROLES__INVENTORY_DIR/inventory_default.yml" "$(dirname "$0")/main.yml" "$@"\n')
    wrapper = tmp_path / "bin/ssh"
    wrapper.write_text("#!/bin/sh\nexec /usr/bin/ssh -F " + shlex.quote(str(tmp_path / ".ssh/config"))
                       + ' -o ProxyJump=none -o ProxyCommand=none "$@"\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(wrapper.parent) + ":" + str(Path(sys.executable).parent) + ":/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setenv("RANGE42_QUIET", "1")
    monkeypatch.setenv("RANGE42_CONFIG_BASE_DIR", str(ws.parent))
    monkeypatch.setenv("ANSIBLE_HOST_KEY_CHECKING", "False")
    for name in ("SSH_AUTH_SOCK", "ANSIBLE_CONFIG", "ANSIBLE_VAULT_PASSWORD_FILE", "ANSIBLE_CALLBACK_PLUGINS",
                 "RANGE42_VAULT_PASSWORD_FILE", "RANGE42_CONTEXT_REPO_LINKS_MANAGED"):
        monkeypatch.delenv(name, raising=False)

    with ssh_guest() as (port, key, executed):
        ssh_config.write_text("Host guest.invalid\n HostName 127.0.0.1\n User fixture\n"
                              f" Port {port}\n HostKeyAlias 127.0.0.1\n IdentityFile /dev/null\n"
                              " GlobalKnownHostsFile /dev/null\n StrictHostKeyChecking no\n")
        (base / "templates/ssh-config.j2").write_text(ssh_config.read_text())
        trusted = "127.0.0.1 " + key.get_name() + " " + key.get_base64() + "\n"
        changed = paramiko.RSAKey.generate(2048)
        original = trusted if pin == "matching" else "127.0.0.1 " + changed.get_name() + " " + changed.get_base64() + "\n" if pin == "changed" else ""
        pins.write_text(original)
        pins.chmod(0o600)
        inventory = yaml.safe_dump({"all": {"hosts": {"guest.invalid": {
            "ansible_connection": "ssh", "ansible_python_interpreter": sys.executable,
            "ARG_vm_ssh_name": "guest.invalid", "deployer_cli_user_ssh_known_hosts": str(pins),
        }}, "children": {"proxmox": {"hosts": {"lab": {"ansible_host": "192.0.2.10:8006"}}}}}})
        (ws / "inventory/inventory_default.yml").write_text(inventory)
        (base / "templates/ansible-inventory.j2").write_text(inventory)
        if caller == "api":
            run = prepare_native_run(tmp_path / "saved", inspect_native_scenario(tmp_path / "saved", "training/exercise"),
                resolve_context("lab-demo", host), scope="full", features={}, parameters={}, artifact_dir=tmp_path / "attempt")
            command = run.command
        else:
            command = ["zsh", "-f", "-c", "source " + shlex.quote(str(script))
                       + "; range42-context use lab demo && range42-context deploy"]
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        assert (result.returncode == 0) is success, output
        assert marker.exists() is success
        assert executed.is_set() is success
        if caller == "api":
            assert pins.read_text() == original, "Managed context selection must not erase or enroll guest pins"
            if not success:
                assert "Host key verification failed" in output
        else:
            assert "127.0.0.1 " + key.get_name() + " " + key.get_base64() in pins.read_text()
