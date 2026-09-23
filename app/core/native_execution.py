"""Prepare an execution view of an existing context for one saved repository.

The installed range42-context selects the environment and runs the lifecycle.
Inventory/SSH templates and bundles come from the saved checkout. Neither the
checkout nor the context's scenario link/inventory is rewritten by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import shutil
import sys

import yaml

from app.core.native_contexts import NativeContext
from app.core.native_scenarios import NATIVE_ACTIONS, invalid, native_variables

_COMMANDS = {"full": "deploy", "configure": "deploy", "deploy_vms": "deploy-vms",
             "teardown": "delete", "delete_vms": "delete-vms", "reset": "reset",
             "deploy_networks": "networks-apply", "delete_networks": "networks-delete-sdn"}


@dataclass(frozen=True)
class NativeRun:
    command: list[str]
    variables_file: Path
    context_dir: Path


def _write(path: Path, content: str, *, executable=False) -> None:
    path.write_text(content)
    path.chmod(0o700 if executable else 0o600)


def prepare_native_run(root: Path, descriptor: dict, context: NativeContext, *,
                       scope: str, features: dict, parameters: dict, artifact_dir: Path,
                       repository_root: Path | None = None) -> NativeRun:
    if scope not in descriptor["actions"]:
        raise invalid("This saved scenario does not declare that lifecycle action", "NATIVE_ACTION_UNAVAILABLE")
    values = native_variables(descriptor, features, parameters)
    shell, ansible, ssh = shutil.which("zsh"), shutil.which("ansible-playbook"), shutil.which("ssh")
    if not shell or not ansible or not ssh:
        raise invalid("Native execution requires zsh, Ansible and SSH on the deployer-cli", "NATIVE_RUNTIME_UNAVAILABLE")
    root, artifact_dir = root.resolve(), artifact_dir.resolve()
    repository_root = (repository_root or root).resolve()
    if not root.is_relative_to(repository_root):
        raise invalid("Native project root must stay inside the saved repository")
    source = root / descriptor["path"]
    view = artifact_dir / "native-context"
    execution_root = artifact_dir / "native-repository"
    execution_project = execution_root / root.relative_to(repository_root)
    scenario = execution_project / descriptor["path"]
    for directory in (artifact_dir, view, view / "inventory", view / "bin"):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Preserve the complete relative layout: private scenarios may import shared
    # playbooks using ../../bundles, and scripts may write generated local files.
    # Copying also prevents those writes from changing the pinned checkout.
    shutil.copytree(repository_root, execution_root, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    # Native playbooks resolve secrets relative to their scenario. Only these
    # credential paths are context-owned; all other assets retain the saved tree.
    for name in ("secrets", "ssh_keys"):
        existing = scenario / name
        if existing.is_symlink() or existing.is_file():
            existing.unlink()
        elif existing.is_dir():
            shutil.rmtree(existing)
        (view / name).symlink_to(context.workspace / name, target_is_directory=True)
        (scenario / name).symlink_to(context.workspace / name, target_is_directory=True)
    (view / "scenario").symlink_to(scenario, target_is_directory=True)
    variables_file = view / "variables.json"
    _write(variables_file, json.dumps(values, allow_nan=False))
    # Append flags to every native Ansible invocation, including older wrappers
    # that do not forward "$@", and reset wrappers that launch other scripts.
    _write(view / "bin/ansible-playbook", "#!/bin/sh\nexec " + shlex.quote(ansible)
           + ' "$@" --extra-vars ' + shlex.quote("@" + str(variables_file)) + "\n", executable=True)
    for action, entrypoint in descriptor["actions"].items():
        suffix = NATIVE_ACTIONS[action][0]
        target = scenario / f"{source.name}.{suffix}"
        entry = scenario / entrypoint
        if target == entry:
            continue
        if target.exists() or target.is_symlink():
            raise invalid("Native lifecycle aliases conflict with a saved scenario file")
        command = ('bash ' + shlex.quote(str(entry)) + ' "$@"') if entry.suffix == ".sh" else (
            'ansible-playbook -i "$RANGE42_ANSIBLE_ROLES__INVENTORY_DIR/inventory_default.yml" '
            '--vault-password-file "$RANGE42_VAULT_PASSWORD_FILE" ' + shlex.quote(str(entry)) + ' "$@"')
        _write(target, "#!/bin/sh\nexec " + command + "\n", executable=True)
    if scope == "configure":
        # The canonical context has no configure command. Its deploy dispatcher
        # runs this attempt's explicit configure entrypoint through a small shim.
        setup = scenario / f"{source.name}.setup.sh"
        setup.unlink(missing_ok=True)
        _write(setup, '#!/bin/sh\nexec bash ' + shlex.quote(str(scenario / f"{source.name}.configure.sh")) + ' "$@"\n', executable=True)
    home = context.workspace.parent.parent
    keys = home / ".ssh/range42" / context.workspace.name
    prepare_vars = {
        "INFRASTRUCTURE_CODENAME": context.codename, "INFRASTRUCTURE_SCENARIO": context.scenario,
        "INFRASTRUCTURE_PROXMOX_ADDRESS": context.address,
        "DEPLOYER_CLI__DST_CONFIG_DIR": str(context.workspace),
        "DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR": str(keys / "backend_keys"),
        "DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR": str(keys / "jump_keys"),
        "DEPLOYER_CLI__DST_SSH_KEYS_STUDENT_DEST_DIR": str(keys / "student_keys"),
        **context.inventory_variables, "ansible_python_interpreter": sys.executable,
    }
    _write(view / "prepare-vars.json", json.dumps(prepare_vars, allow_nan=False))
    tasks = [{"name": "Render saved scenario inventory for the selected context", "ansible.builtin.template": {
        "src": str(source / descriptor["inventory_template"]), "dest": str(view / "inventory/inventory_default.yml"), "mode": "0600"}}]
    ssh_template = source / "templates/ssh-config.j2"
    ssh_config = view / "ssh-config"
    if ssh_template.is_file():
        tasks.append({"name": "Render saved scenario SSH configuration", "ansible.builtin.template": {
            "src": str(ssh_template), "dest": str(ssh_config), "mode": "0600"}})
    else:
        original_ssh = home / ".ssh" / f"config_range42-{context.workspace.name}"
        if original_ssh.is_file():
            shutil.copyfile(original_ssh, ssh_config)
            ssh_config.chmod(0o600)
    _write(view / "prepare.yml", yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": tasks}]))
    environment = {
        "RANGE42_ACTIVE_CONFIG_DIR": str(view), "RANGE42_CONFIG__ROOT_DIR": str(view),
        "RANGE42_SOURCE_CONTEXT_DIR": str(context.workspace), "RANGE42_ACTIVE_WORKSPACE": context.workspace.name,
        "RANGE42_ANSIBLE_ROLES__INVENTORY_DIR": str(view / "inventory"),
        "RANGE42_VAULT_PASSWORD_FILE": str(context.workspace / "secrets/vault_pass.txt"),
        "ANSIBLE_VAULT_PASSWORD_FILE": str(context.workspace / "secrets/vault_pass.txt"),
    }
    if descriptor["bundle_path"]:
        environment["RANGE42_BUNDLE_DIR"] = str(execution_project / descriptor["bundle_path"])
    use_ssh = ssh_template.is_file() or ssh_config.is_file()
    if use_ssh:
        environment["ANSIBLE_SSH_COMMON_ARGS"] = "-F " + shlex.quote(str(ssh_config))
        _write(view / "bin/ssh", "#!/bin/sh\nexec " + shlex.quote(ssh) + " -F "
               + shlex.quote(str(ssh_config)) + ' "$@"\n', executable=True)
    lines = [f"export RANGE42_CONFIG_BASE_DIR={shlex.quote(str(context.workspace.parent))}",
             f"source {shlex.quote(str(context.context_script))} || exit $?",
             "range42-context use " + shlex.join([context.codename, context.scenario]) + " || exit $?",
             # Confirm the canonical command selected exactly the registered workspace.
             '[[ "${RANGE42_ACTIVE_CONFIG_DIR:A}" == ' + shlex.quote(str(context.workspace)) + ' ]] || exit 71',
             *[f"export {name}={shlex.quote(value)}" for name, value in environment.items()],
             f"export PATH={shlex.quote(str(view / 'bin'))}:\"$PATH\"",
             shlex.join([ansible, "-i", "localhost,", "-c", "local", str(view / "prepare.yml"),
                         "--extra-vars", "@" + str(view / "prepare-vars.json")]) + " || exit $?",
             "range42-context " + _COMMANDS[scope], "exit $?"]
    launch = view / "run.zsh"
    _write(launch, "\n".join(lines) + "\n")
    return NativeRun([shell, "-f", str(launch)], variables_file, view)
