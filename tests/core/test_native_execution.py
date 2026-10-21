import json
import asyncio
import os
import signal
import subprocess

import pytest

from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario


def test_native_adapter_selects_runner_owned_secret_links(tmp_path, monkeypatch):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    ws, script, host = context(tmp_path, monkeypatch)
    monkeypatch.delenv("RANGE42_QUIET", raising=False)
    script.write_text("""[[ "$RANGE42_QUIET" == 1 ]] || return 72
    range42-context() {
      if [[ "$1" == use ]]; then
        [[ "$RANGE42_CONTEXT_REPO_LINKS_MANAGED" == YES ]] || return 73
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """)
    repo = tmp_path / "repo"
    base = scenario(repo)
    result = tmp_path / "ran"
    (base / "exercise.setup.sh").write_text('printf native > "$TEST_NATIVE_RESULT"\n')
    run = prepare_native_run(
        repo,
        inspect_native_scenario(repo, "training/exercise-a"),
        resolve_context("lab-demo", host),
        scope="full",
        features={},
        parameters={},
        artifact_dir=tmp_path / "run",
    )
    completed = subprocess.run(
        run.command,
        env={**os.environ, "TEST_NATIVE_RESULT": str(result)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert result.read_text() == "native"
    assert not (base / "secrets").exists()
    assert (run.context_dir / "scenario/secrets").resolve() == ws / "secrets"


def test_native_execution_binds_context_and_pinned_dependencies_without_editing_sources(
    tmp_path, monkeypatch
):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text("""range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
        source "$RANGE42_ACTIVE_CONFIG_DIR/sourced_range42.sh"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """)
    repo = tmp_path / "repo"
    base = scenario(repo)
    (repo / "bundles").mkdir()
    (base / "templates/ansible-inventory.j2").write_text(
        "all:\n  hosts:\n    {{ INFRASTRUCTURE_CODENAME }}:\n      ansible_host: {{ INFRASTRUCTURE_PROXMOX_ADDRESS }}\n"
    )
    result = tmp_path / "result.json"
    wrapper = base / "exercise.setup.sh"
    wrapper.write_text("""#!/bin/bash
python3 - <<'PY'
import json,os
from pathlib import Path
Path(os.environ['TEST_NATIVE_RESULT']).write_text(json.dumps({
 'context':os.environ['RANGE42_ACTIVE_CONFIG_DIR'],
 'bundles':os.environ['RANGE42_BUNDLE_DIR'],
 'inventory':Path(os.environ['RANGE42_ANSIBLE_ROLES__INVENTORY_DIR'],'inventory_default.yml').read_text(),
}))
PY
""")
    before = (
        wrapper.read_bytes(),
        (ws / "scenario").readlink(),
        (ws / "inventory/inventory_default.yml").read_bytes(),
    )
    descriptor = inspect_native_scenario(repo, "training/exercise-a")
    run = prepare_native_run(
        repo,
        descriptor,
        resolve_context("lab-demo", host),
        scope="full",
        features={"WAZUH": True},
        parameters={},
        artifact_dir=tmp_path / "run",
    )
    completed = subprocess.run(
        run.command,
        env={**os.environ, "TEST_NATIVE_RESULT": str(result)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    proof = json.loads(result.read_text())
    assert proof["bundles"] == str(tmp_path / "run/native-repository/bundles")
    assert "lab:" in proof["inventory"] and "192.0.2.10" in proof["inventory"]
    assert proof["context"] != str(ws)
    assert (
        wrapper.read_bytes(),
        (ws / "scenario").readlink(),
        (ws / "inventory/inventory_default.yml").read_bytes(),
    ) == before
    assert json.loads(run.variables_file.read_text()) == {"INSTALL_WAZUH": "YES"}


@pytest.mark.asyncio
async def test_detached_native_command_uses_existing_runner_event_and_exit_lifecycle(
    tmp_path,
):
    from app.core.runner_detached import DetachedRunner

    art = tmp_path / "attempt"
    result = tmp_path / "ran"
    command = [
        "/bin/sh",
        "-c",
        'printf native > "$1"; echo native-run-completed',
        "native-test",
        str(result),
    ]
    handle = await DetachedRunner().start(
        private_data_dir=art, extravars={"r42_native_command": command}, envvars={}
    )
    assert await handle.wait() == 0
    assert result.read_text() == "native"
    assert (art / "rc").read_text().strip() == "0"
    assert list((art / "job_events").glob("*.json"))


@pytest.mark.asyncio
async def test_native_context_keeps_runner_callbacks_when_cli_sets_its_own(
    tmp_path, monkeypatch
):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario
    from app.core.runner_detached import DetachedRunner

    ws, script, host = context(tmp_path, monkeypatch)
    callbacks = tmp_path / "cli-callbacks"
    callbacks.mkdir()
    script.write_text(
        '''range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
        source "$RANGE42_ACTIVE_CONFIG_DIR/sourced_range42.sh"
        export ANSIBLE_CALLBACK_PLUGINS="'''
        + str(callbacks)
        + """"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """
    )
    repo = tmp_path / "repo"
    base = scenario(repo)
    (base / "main.yml").write_text("""- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.debug:
        msg: native-callback-proof
""")
    (base / "exercise.setup.sh").write_text(
        "ansible-playbook -i localhost, -c local ./main.yml\n"
    )
    artifact = tmp_path / "attempt"
    run = prepare_native_run(
        repo,
        inspect_native_scenario(repo, "training/exercise-a"),
        resolve_context("lab-demo", host),
        scope="full",
        features={},
        parameters={},
        artifact_dir=artifact,
    )
    handle = await DetachedRunner().start(
        private_data_dir=artifact,
        extravars={"r42_native_command": run.command},
        envvars={},
    )
    result = await handle.wait()
    events = [
        json.loads(path.read_text())
        for path in (artifact / "job_events").glob("*.json")
    ]
    output = "\n".join(event.get("stdout", "") for event in events)
    assert result == 0, output
    assert "native-callback-proof" in output


@pytest.mark.parametrize("subdir", ["", "projects/team"])
def test_native_execution_preserves_relative_shared_dependencies_and_context_secrets(
    tmp_path, monkeypatch, subdir
):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text("""range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """)
    repo = tmp_path / "repo"
    project_root = repo / subdir
    base = scenario(project_root)
    (repo / "bundles").mkdir()
    (repo / "bundles/shared.yml").write_text("""- ansible.builtin.assert:
    that:
      - INSTALL_WAZUH == 'YES'
      - lookup('file', 'secrets/fixture') == 'context-owned'
""")
    (ws / "secrets/fixture").write_text("context-owned")
    (base / "main.yml").write_text(
        """- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.include_tasks: """
        + os.path.relpath(repo / "bundles/shared.yml", base)
        + "\n"
    )
    # Older wrappers ignore "$@"; feature selections must still reach Ansible.
    (base / "exercise.setup.sh").write_text(
        "ansible-playbook -i localhost, -c local ./main.yml\n"
    )
    run = prepare_native_run(
        project_root,
        inspect_native_scenario(project_root, "training/exercise-a"),
        resolve_context("lab-demo", host),
        scope="full",
        features={"WAZUH": True},
        parameters={},
        artifact_dir=tmp_path / "run",
        repository_root=repo,
    )
    completed = subprocess.run(run.command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (base / "secrets").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_native_cancellation_stops_the_command_and_its_child(
    tmp_path, recovered, nested
):
    from app.core.runner_detached import (
        DetachedRunner,
        _process_identity,
        signal_process_identity,
        signal_running_attempt,
    )

    artifact = tmp_path / "runner/attempt"
    proof = tmp_path / "children"
    handle = await DetachedRunner().start(
        private_data_dir=artifact,
        extravars={
            "r42_native_command": [
                "/bin/sh",
                "-c",
                (
                    'sleep 60 & echo "$$ $!" > "$1"; wait'
                    if nested
                    else 'echo "$$" > "$1"; exec sleep 60'
                ),
                "cancel-test",
                str(proof),
            ]
        },
        envvars={},
    )
    identities = []
    try:
        for _ in range(100):
            if proof.is_file():
                break
            await asyncio.sleep(0.05)
        assert proof.is_file(), "native command did not start"
        identities = [_process_identity(int(pid)) for pid in proof.read_text().split()]
        assert all(identities)
        if recovered:
            assert await signal_running_attempt(tmp_path)
        else:
            await handle.kill()
        await asyncio.wait_for(handle.wait(), 10)
        for _ in range(40):
            if all(
                _process_identity(identity["pid"]) != identity
                for identity in identities
            ):
                break
            await asyncio.sleep(0.05)
        assert all(
            _process_identity(identity["pid"]) != identity for identity in identities
        ), "deployment command survived Cancel"
        assert (artifact / "status").read_text() == "canceled"
    finally:
        await handle.kill()
        for identity in identities:
            if identity:
                signal_process_identity(identity, signal.SIGKILL)


@pytest.mark.parametrize(
    "pin,success", [("matching", True), ("empty", False), ("changed", False)]
)
def test_native_direct_ssh_uses_workspace_pins_even_without_inventory_ssh_options(
    tmp_path, monkeypatch, pin, success
):
    from tests.core.test_native_cli_ssh import ssh_guest
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario
    import paramiko

    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text("""range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """)
    base = scenario(tmp_path / "repo")
    (base / "exercise.setup.sh").write_text("exec ssh fixture.invalid true\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    with ssh_guest() as (port, key, executed):
        (base / "templates/ssh-config.j2").write_text(
            "Host fixture.invalid\n HostName 127.0.0.1\n HostKeyAlias fixture.invalid\n"
            f" Port {port}\n User fixture\n GlobalKnownHostsFile /dev/null\n"
            " StrictHostKeyChecking no\n UserKnownHostsFile /dev/null\n"
        )
        wrong = paramiko.RSAKey.generate(2048)
        trusted = key if pin == "matching" else wrong
        (ws / "ssh_keys/known_hosts").write_text(
            ""
            if pin == "empty"
            else "fixture.invalid "
            + trusted.get_name()
            + " "
            + trusted.get_base64()
            + "\n"
        )
        run = prepare_native_run(
            tmp_path / "repo",
            inspect_native_scenario(tmp_path / "repo", "training/exercise-a"),
            resolve_context("lab-demo", host),
            scope="full",
            features={},
            parameters={},
            artifact_dir=tmp_path / "run",
        )
        result = subprocess.run(run.command, capture_output=True, text=True, timeout=25)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert executed.is_set() == success


def test_native_devkit_uses_an_attempt_private_credential_view(tmp_path, monkeypatch):
    from pathlib import Path
    import shlex
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    contract = os.environ.get("RANGE42_NATIVE_CONTRACT_FIXTURE")
    production = (
        Path(contract) / "devkit"
        if contract
        else Path(
            os.getenv(
                "RANGE42_DEVKIT_SOURCE",
                str(
                    Path(__file__).resolve().parents[3]
                    / "range42-ansible_roles-debug-devkit"
                ),
            )
        )
    )
    warmup = production / "proxmox__inc.warmup_checks.sh"
    if not warmup.is_file():
        pytest.skip("Reviewed native devkit checkout is unavailable")
    ws, script, host = context(tmp_path, monkeypatch)
    (ws / "secrets/default_vault.yml").write_text("selected-workspace-only")
    source = tmp_path / "read-only-devkit"
    source.mkdir()
    (source / warmup.name).write_bytes(warmup.read_bytes())
    (source / warmup.name).chmod(0o555)
    error = source / "devkit_utils.text.echo_error.to.text.to.stderr.sh"
    error.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >&2\n')
    error.chmod(0o555)
    foreign = tmp_path / "other-credentials"
    foreign.mkdir()
    (foreign / "default_vault.yml").write_text("DO-NOT-USE")
    (source / "secrets").symlink_to(foreign, target_is_directory=True)
    script.write_text(
        """range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
        export RANGE42_ANSIBLE_ROLES__DEVKITS_DIR="""
        + shlex.quote(str(source))
        + """
        export ANSIBLE_ROLES_PATH=/fixture-role
        export PATH="$RANGE42_ANSIBLE_ROLES__DEVKITS_DIR:$PATH"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """
    )
    repo = tmp_path / "repo"
    base = scenario(repo)
    (base / "exercise.setup.sh").write_text("""set -eu
proxmox__inc.warmup_checks.sh
test "$(cat "$RANGE42_ANSIBLE_ROLES__DEVKITS_DIR/secrets/default_vault.yml")" = selected-workspace-only
""")
    source.chmod(0o555)
    before = (source / "secrets").readlink()
    for index in range(2):
        run = prepare_native_run(
            repo,
            inspect_native_scenario(repo, "training/exercise-a"),
            resolve_context("lab-demo", host),
            scope="full",
            features={},
            parameters={},
            artifact_dir=tmp_path / f"attempt-{index}",
        )
        completed = subprocess.run(
            run.command, capture_output=True, text=True, timeout=25
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (source / "secrets").readlink() == before
    assert (foreign / "default_vault.yml").read_text() == "DO-NOT-USE"


def test_native_devkit_inventory_query_keeps_json_and_registered_node_binding(
    tmp_path, monkeypatch
):
    from pathlib import Path
    import shutil
    import shlex
    import yaml
    from ansible.parsing.vault import VaultLib, VaultSecret
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    contract = os.environ.get("RANGE42_NATIVE_CONTRACT_FIXTURE")
    production = (
        Path(contract) / "devkit"
        if contract
        else Path(
            os.getenv(
                "RANGE42_DEVKIT_SOURCE",
                str(
                    Path(__file__).resolve().parents[3]
                    / "range42-ansible_roles-debug-devkit"
                ),
            )
        )
    )
    if not (production / "proxmox_vm.list.to.jsons.sh").is_file():
        pytest.skip("Reviewed native devkit checkout is unavailable")
    ws, script, host = context(tmp_path, monkeypatch)
    host.node_name = "selected-native-node"
    source = tmp_path / "devkit"
    shutil.copytree(
        production,
        source,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "secrets", "__pycache__"),
    )
    (source / "secrets").symlink_to(
        tmp_path / "foreign-secrets", target_is_directory=True
    )
    vault = {
        "proxmox_node": host.node_name,
        "proxmox_api_host": "192.0.2.10:8006",
        "proxmox_api_user": "fixture@pve",
        "proxmox_api_token_id": "fixture",
        "proxmox_api_token_secret": "FIXTURE-NOT-REAL",
        "proxmox_api_validate_certs": True,
    }
    (ws / "secrets/default_vault.yml").write_bytes(
        VaultLib().encrypt(yaml.safe_dump(vault).encode(), VaultSecret(b"not-public"))
    )
    roles = tmp_path / "roles"
    role = roles / "range42-ansible_roles-proxmox_controller/tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text(
        yaml.safe_dump(
            [
                {
                    "ansible.builtin.debug": {"var": "vm_list"},
                    "vars": {
                        "vm_list": [
                            {
                                "vm_id": 1250,
                                "vm_name": "fixture-guest",
                                "proxmox_node": host.node_name,
                            }
                        ]
                    },
                }
            ]
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    contacted = tmp_path / "unexpected-fast-path"
    curl.write_text("#!/bin/sh\ntouch " + shlex.quote(str(contacted)) + "\nexit 1\n")
    curl.chmod(0o755)
    script.write_text(
        """range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
        export RANGE42_ANSIBLE_ROLES__DEVKITS_DIR="""
        + shlex.quote(str(source))
        + """
        export ANSIBLE_ROLES_PATH="""
        + shlex.quote(str(roles))
        + """
        export PATH="$RANGE42_ANSIBLE_ROLES__DEVKITS_DIR:$PATH"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    """
    )
    result = tmp_path / "inventory.json"
    repo = tmp_path / "repo"
    base = scenario(repo)
    (base / "exercise.setup.sh").write_text(
        "set -eu\nprintf 'selected-native-node\\n' | proxmox_vm.list.to.jsons.sh > "
        + shlex.quote(str(result))
        + '\ntest "$ANSIBLE_STDOUT_CALLBACK" = default\n'
    )
    monkeypatch.setenv("ANSIBLE_STDOUT_CALLBACK", "default")
    monkeypatch.setenv("PATH", str(fake_bin) + ":" + os.environ["PATH"])
    run = prepare_native_run(
        repo,
        inspect_native_scenario(repo, "training/exercise-a"),
        resolve_context("lab-demo", host),
        scope="full",
        features={},
        parameters={},
        artifact_dir=tmp_path / "attempt",
    )
    completed = subprocess.run(run.command, capture_output=True, text=True, timeout=40)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result.read_text().strip(), completed.stdout + completed.stderr
    assert json.loads(result.read_text())["vm_id"] == 1250
    assert not contacted.exists(), (
        "Native API operations must use the configured, TLS-verified Ansible controller"
    )



def test_bound_native_inventory_cannot_inherit_the_runtime_templates_old_target(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    import shutil
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario

    workspace, _, host = context(tmp_path, monkeypatch)
    original = (workspace / 'inventory/inventory_default.yml').read_bytes()
    selected = replace(resolve_context('lab-demo', host), address='192.0.2.20',
        target_api_url='https://192.0.2.20:8443', target_node='node-b', node_name='node-b',
        inventory_variables={'INFRASTRUCTURE_PROXMOX_ADDRESS': '192.0.2.10',
            'INFRASTRUCTURE_PROXMOX_API_AUTHORITY': '192.0.2.10:8006'})
    repo = tmp_path / 'repo'
    base = scenario(repo)
    (base / 'templates/ansible-inventory.j2').write_text(
        'all:\n  vars:\n    target: {{ INFRASTRUCTURE_PROXMOX_ADDRESS }}\n    api: {{ INFRASTRUCTURE_PROXMOX_API_AUTHORITY }}\n')
    run = prepare_native_run(repo, inspect_native_scenario(repo, 'training/exercise-a'), selected,
        scope='full', features={}, parameters={}, artifact_dir=tmp_path / 'attempt')
    rendered = subprocess.run([shutil.which('ansible-playbook'), '-i', 'localhost,', '-c', 'local',
        str(run.context_dir / 'prepare.yml'), '--extra-vars', '@'+str(run.context_dir / 'prepare-vars.json')],
        capture_output=True, text=True, timeout=20)
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    inventory = Path(run.context_dir / 'inventory/inventory_default.yml').read_text()
    assert '192.0.2.20' in inventory and '192.0.2.20:8443' in inventory
    assert '192.0.2.10' not in inventory
    assert (workspace / 'inventory/inventory_default.yml').read_bytes() == original
