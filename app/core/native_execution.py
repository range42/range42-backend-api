"""Prepare an execution view of an existing context for one saved repository.

The installed range42-context selects the environment and runs the lifecycle.
Inventory/SSH templates and bundles come from the saved checkout. Neither the
checkout nor the context's scenario link/inventory is rewritten by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import shutil
import sys
from urllib.parse import urlsplit

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
                       repository_root: Path | None = None, workshop_plan: dict | None = None,
                       workshop_credentials: dict | None = None, workshop_seat: int = 0,
                       workshop_state_dir: Path | None = None, foundation_request: dict | None = None,
                       foundation_agent_revision: str = '', foundation_target_host_id: str = '',
                       environment_network_bundle: dict | None = None) -> NativeRun:
    if scope not in descriptor["actions"]:
        raise invalid("This saved scenario does not declare that lifecycle action", "NATIVE_ACTION_UNAVAILABLE")
    from app.core.native_contexts import execution_issue
    if not foundation_request and (issue := execution_issue(context, scope)):
        raise invalid(issue, 'WORKSHOP_INFRASTRUCTURE_UNQUALIFIED')
    if foundation_request and (not descriptor.get('preparation') or scope != 'full' or workshop_plan
            or foundation_request.get('plan', {}).get('environment_id') != context.id
            or foundation_request['plan'].get('target_node') != context.target_node
            or not re.fullmatch(r'[0-9a-f]{64}', foundation_agent_revision)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', foundation_target_host_id)):
        raise invalid('The original foundation request must match this selected target and preparation action.', 'FOUNDATION_ORIGINAL_CHANGED')
    if environment_network_bundle is not None and (not foundation_request
            or environment_network_bundle.get('request', {}).get('foundation_request') != foundation_request
            or environment_network_bundle['request'].get('plan', {}).get('environment_id') != context.id
            or environment_network_bundle['request']['plan'].get('node') != context.target_node
            or not re.fullmatch(r'[0-9a-f]{64}', environment_network_bundle.get('transport_revision', ''))):
        raise invalid('Optional environment networks must retain the same original foundation and target.', 'ENVIRONMENT_NETWORK_ORIGINAL_CHANGED')
    values = native_variables(descriptor, features, parameters)
    if context.target_api_url:
        values.update(proxmox_api_host=urlsplit(context.target_api_url).netloc,
                      proxmox_node=context.target_node, proxmox_api_validate_certs=True)
    if workshop_plan or foundation_request:
        # The same registered credential used for admission must perform writes.
        # Old context vaults may contain a broader, unrelated Proxmox identity.
        try:
            identity, secret = context.provisioning_credential.split('=', 1)
            user, token_id = identity.split('!', 1)
            if not user or not token_id or not secret or any(char.isspace() for char in context.provisioning_credential):
                raise ValueError()
        except (AttributeError, ValueError):
            raise invalid('The selected workshop target requires its own valid Proxmox API credential',
                          'WORKSHOP_PROVISIONING_CREDENTIAL_REQUIRED') from None
        values.update(proxmox_api_user=user, proxmox_api_token_id=token_id, proxmox_api_token_secret=secret)
    if workshop_plan:
        if type(workshop_seat) is not int or not 0 <= workshop_seat <= workshop_plan['seats']:
            raise invalid('Select a participant allocated to this workshop')
        values.update(range42_workshop_plan=workshop_plan,
                      range42_workshop_participants=workshop_credentials or {},
                      range42_workshop_selected_seat=workshop_seat,
                      range42_workshop_receipt_root=str(workshop_state_dir or artifact_dir / 'workshop-state'),
                      proxmox_dest_vm_storage_name=workshop_plan['storage'],
                      proxmox_api_validate_certs=True, proxmox_node=context.node_name)
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
    if foundation_request:
        request_path = view / 'foundation-request.json'
        payload = {'protocol': 'range42-foundation-attempt-v1', 'foundation': foundation_request,
            'environment_networks': environment_network_bundle} if environment_network_bundle is not None else foundation_request
        _write(request_path, json.dumps(payload, allow_nan=False))
        values.update(range42_foundation_request_file=str(request_path),
                      range42_foundation_agent_revision=foundation_agent_revision,
                      range42_foundation_target_host_id=foundation_target_host_id,
                      range42_foundation_python=sys.executable,
                      range42_foundation_runner_file=str(Path(__file__).with_name('foundation_runner.py')))
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
    devkit = view / "native-devkit"
    devkit_inventory = view / "devkit-inventory.yml"
    if context.node_name:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", context.node_name):
            raise invalid("The registered Proxmox node name is invalid")
        _write(devkit_inventory, yaml.safe_dump({"all": {"hosts": {context.node_name: {
            "ansible_connection": "local", "ansible_python_interpreter": sys.executable,
        }}}}))
    # Append flags to every native Ansible invocation, including older wrappers
    # that do not forward "$@", and reset wrappers that launch other scripts.
    devkit_args = " -i " + shlex.quote(str(devkit_inventory)) if context.node_name else ""
    _write(view / "bin/ansible-playbook", "#!/bin/sh\n"
           + 'if [ "${ANSIBLE_CONFIG:-}" = ' + shlex.quote(str(devkit / "ansible_no_skipped_json.cfg")) + ' ]; then\n'
           + "  export ANSIBLE_STDOUT_CALLBACK=ansible.posix.json\n  unset ANSIBLE_CALLBACK_PLUGINS\n"
           + "  exec " + shlex.quote(ansible) + ' "$@"' + devkit_args
           + " --extra-vars " + shlex.quote("@" + str(variables_file)) + "\nfi\nexec " + shlex.quote(ansible)
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
        "INFRASTRUCTURE_PROXMOX_API_AUTHORITY": urlsplit(context.target_api_url).netloc if context.target_api_url else context.address + ':8006',
        "DEPLOYER_CLI__DST_CONFIG_DIR": str(context.workspace),
        "DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR": str(keys / "backend_keys"),
        "DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR": str(keys / "jump_keys"),
        "DEPLOYER_CLI__DST_SSH_KEYS_STUDENT_DEST_DIR": str(keys / "student_keys"),
        **context.inventory_variables, "ansible_python_interpreter": sys.executable,
    }
    if context.target_api_url:
        # A reused runtime supplies code and keys, never its earlier target.
        prepare_vars.update(INFRASTRUCTURE_PROXMOX_ADDRESS=context.address,
            INFRASTRUCTURE_PROXMOX_API_AUTHORITY=urlsplit(context.target_api_url).netloc)
    if workshop_plan:
        prepare_vars['range42_workshop_plan'] = workshop_plan
        prepare_vars['range42_workshop_known_hosts'] = str(context.workspace / 'ssh_keys/known_hosts')
        prepare_vars['range42_workshop_source_ssh_config'] = str(home / '.ssh' / f'config_range42-{context.workspace.name}')
    _write(view / "prepare-vars.json", json.dumps(prepare_vars, allow_nan=False))
    tasks = [{"name": "Render saved scenario inventory for the selected context", "ansible.builtin.template": {
        "src": str(source / descriptor["inventory_template"]), "dest": str(view / "inventory/inventory_default.yml"), "mode": "0600"}}]
    ssh_template = source / "templates/ssh-config.j2"
    ssh_config = view / "scenario-ssh-config"
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
        # Context files can select CLI defaults; restore the API's verified
        # connection policy after sourcing them and before running Ansible.
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        # The reviewed Ansible controller honors the context's certificate
        # policy. Its JSON helpers must not fall back to legacy curl -k calls.
        "RANGE42_PROXMOX_API_FORCE": "off",
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
        # Put verified context trust before saved Host directives. The included
        # configuration also reaches ProxyJump subprocesses, which do not inherit
        # the outer client's command-line known_hosts options.
        policy = view / "ssh-config"
        known_hosts = context.workspace / "ssh_keys/known_hosts"
        _write(policy, "Host *\n  StrictHostKeyChecking yes\n  UserKnownHostsFile "
               + json.dumps(str(known_hosts)) + "\nInclude " + json.dumps(str(ssh_config)) + "\n")
        ssh_config = policy
        environment["ANSIBLE_SSH_COMMON_ARGS"] = "-F " + shlex.quote(str(ssh_config))
        _write(view / "bin/ssh", "#!/bin/sh\nexec " + shlex.quote(ssh) + " -F "
               + shlex.quote(str(ssh_config)) + ' "$@"\n', executable=True)
    prepare_devkit = view / "prepare-devkit.py"
    _write(prepare_devkit, '''import os,sys,shutil
from pathlib import Path
source=os.environ.get('RANGE42_ANSIBLE_ROLES__DEVKITS_DIR')
if source:
    source=Path(source).resolve()
    if not source.is_dir():
        raise SystemExit('The selected native devkit directory is unavailable')
    destination=Path(sys.argv[1])
    shutil.copytree(source,destination,symlinks=True,
                    ignore=shutil.ignore_patterns('.git','secrets','__pycache__'))
    destination.chmod(0o700)
    (destination/'secrets').symlink_to(Path(sys.argv[2])/'secrets',target_is_directory=True)
''')
    lines = ['r42_runner_callbacks="${ANSIBLE_CALLBACK_PLUGINS:-}"',
             f"export RANGE42_CONFIG_BASE_DIR={shlex.quote(str(context.workspace.parent))}",
             "export RANGE42_QUIET=1",
             f"source {shlex.quote(str(context.context_script))} || exit $?",
             # Scenario credentials already belong to this attempt's copied
             # repository; context selection must not edit installed sources.
             "export RANGE42_CONTEXT_REPO_LINKS_MANAGED=YES",
             "range42-context use " + shlex.join([context.codename, context.scenario]) + " || exit $?",
             # The CLI selects its own display plugins. Retain the outer runner's
             # awx_display plugin so nested Ansible still publishes runner events.
             'export ANSIBLE_CALLBACK_PLUGINS="${r42_runner_callbacks}${r42_runner_callbacks:+:}${ANSIBLE_CALLBACK_PLUGINS:-}"',
             # Confirm the canonical command selected exactly the registered workspace.
             '[[ "${RANGE42_ACTIVE_CONFIG_DIR:A}" == ' + shlex.quote(str(context.workspace)) + ' ]] || exit 71',
             *[f"export {name}={shlex.quote(value)}" for name, value in environment.items()],
             f"export PATH={shlex.quote(str(view / 'bin'))}:\"$PATH\"",
             # Native shell tools require their own secrets link to identify the
             # active context. Keep that link in this attempt, never in the
             # shared read-only runtime or another registered workspace.
             shlex.join([sys.executable, str(prepare_devkit), str(devkit), str(context.workspace)]) + " || exit $?",
             "if [[ -d " + shlex.quote(str(devkit)) + " ]]; then",
             "export RANGE42_ANSIBLE_ROLES__DEVKITS_DIR=" + shlex.quote(str(devkit)),
             "export PATH=" + shlex.quote(str(view / "bin")) + ":" + shlex.quote(str(devkit)) + ':"$PATH"',
             "fi",
             shlex.join([ansible, "-i", "localhost,", "-c", "local", str(view / "prepare.yml"),
                         "--extra-vars", "@" + str(view / "prepare-vars.json")]) + " || exit $?",
             "range42-context " + _COMMANDS[scope], "exit $?"]
    launch = view / "run.zsh"
    _write(launch, "\n".join(lines) + "\n")
    return NativeRun([shell, "-f", str(launch)], variables_file, view)
