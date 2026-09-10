"""Resolve source-pinned VM bundles and validate their generated scenario calls."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import re

from cryptography.fernet import InvalidToken
import yaml

from app.core.bundle_runtime import canonical, dependencies, runtime_snapshot, tree_digest
from app.core.catalog_index import detail_at_path
from app.core.credential_store import credential_cipher
from app.core.errors import Range42Error

_TARGETS = {'global_vm_ssh_name', 'global_vm_ci_ip', 'target_ansible_host', 'target_group'}
_BUNDLE_PREFIX = "{{ lookup('env', 'RANGE42_BUNDLE_DIR') }}/"
_ENV_PATTERN = re.compile(r"lookup\(\s*['\"]env['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)")
_PATH = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+')


def invalid(message: str, code: str = 'BUNDLE_ATTACHMENT_INVALID') -> Range42Error:
    return Range42Error(code=code, error='bundle_attachment_invalid', status=409, message=message)


def _path(value: str) -> None:
    if not isinstance(value, str) or not _PATH.fullmatch(value) or any(part in {'.', '..'} for part in value.split('/')):
        raise invalid('Bundle paths must remain inside their source directory')


def _bounded_document(path: Path, root: Path, *, yaml_document: bool = False):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2 * 1024 * 1024:
        raise invalid('Bundle manifest and playbook paths must be bounded project files')
    try:
        return yaml.safe_load(path.read_text()) if yaml_document else json.loads(path.read_text())
    except (OSError, ValueError, yaml.YAMLError):
        raise invalid('Cannot parse the bundle manifest or playbook') from None


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _check_bundle_scope(base: Path, profile: dict) -> None:
    """Reject sibling/dynamic imports; role dependencies are the installed profile."""
    roles = profile['environment']['ANSIBLE_ROLES_PATH'].split(':')
    names = set()
    for path in sorted(base.rglob('*')):
        if path.suffix not in {'.yml', '.yaml'} or path.name.endswith('.src.yml'):
            continue
        try:
            value = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            raise invalid('Bundle YAML cannot be validated') from None
        text = '\n'.join(_strings(value))
        literal_env = _ENV_PATTERN.findall(text)
        if len(re.findall(r"lookup\(\s*['\"]env['\"]\s*,", text)) != len(literal_env):
            raise invalid('Environment dependencies must use literal names', 'BUNDLE_SCOPE_UNSUPPORTED')
        for name in literal_env:
            if name == 'RANGE42_BUNDLE_DIR':
                raise invalid('Sibling bundle lookups require dependency-closure support; this attachment is unsupported', 'BUNDLE_SCOPE_UNSUPPORTED')
            if name != 'RANGE42_ACTIVE_CONFIG_DIR':
                if name not in profile['environment']:
                    raise invalid(f'Bundle runtime dependency environment is not configured: {name}', 'BUNDLE_DEPENDENCY_UNAVAILABLE')
                roots = [Path(component['root']).resolve() for component in profile['components'].values()]
                if any(not any(Path(value).resolve().is_relative_to(root) for root in roots)
                       for value in profile['environment'][name].split(':')):
                    raise invalid(f'Bundle dependency can reach code outside the installed profile: {name}', 'BUNDLE_DEPENDENCY_UNAVAILABLE')
        for node in _walk(value):
            for key, item in node.items():
                action = key.removeprefix('ansible.builtin.')
                if action == 'import_playbook':
                    raise invalid('Imported playbooks require dependency-closure support; this attachment is unsupported', 'BUNDLE_SCOPE_UNSUPPORTED')
                if action in {'include_tasks', 'import_tasks'}:
                    file = item.get('file') if isinstance(item, dict) else item
                    if not isinstance(file, str) or '{{' in file or '{%' in file or not (path.parent / file).resolve().is_relative_to(base.resolve()):
                        raise invalid('Task imports must be literal paths inside the selected bundle', 'BUNDLE_SCOPE_UNSUPPORTED')
                if action in {'include_role', 'import_role'}:
                    names.add(item.get('name') if isinstance(item, dict) else item)
                if action == 'roles' and isinstance(item, list):
                    names.update(role.get('role') if isinstance(role, dict) else role for role in item)
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise invalid('Dynamic role names are not supported in bundle attachments', 'BUNDLE_SCOPE_UNSUPPORTED')
        if not any((Path(root) / name / 'tasks' / filename).is_file() for root in roles for filename in ('main.yml', 'main.yaml')):
            raise invalid(f'Bundle runtime role is unavailable: {name}', 'BUNDLE_DEPENDENCY_UNAVAILABLE')


def _single_vm_group_binding(base: Path, entrypoint: str) -> None:
    """Narrow only a static selector; installed role code remains trusted runtime."""
    plays = _bounded_document(base / Path(entrypoint).name, base, yaml_document=True)
    if (not isinstance(plays, list) or not plays or not all(
            isinstance(play, dict) and isinstance(play.get('hosts'), str)
            and re.fullmatch(r'{{\s*target_group\s*}}', play['hosts']) for play in plays)):
        raise invalid('Single-VM group binding requires every play to use exactly hosts: "{{ target_group }}"', 'BUNDLE_SCOPE_UNSUPPORTED')
    for path in base.rglob('*'):
        if path.suffix not in {'.yml', '.yaml'} or path.name.endswith('.src.yml'):
            continue
        document = _bounded_document(path, base, yaml_document=True)
        for node in _walk(document):
            for key, value in node.items():
                action = str(key).removeprefix('ansible.builtin.')
                if (action in {'delegate_to', 'local_action', 'add_host', 'group_by', 'target_group', 'vars_files', 'include_vars'}
                        or action.lower().startswith('ansible_')
                        or action == 'connection' and value != 'ssh'):
                    raise invalid('Single-VM group bundles cannot change delegation, inventory or the managed target selector', 'BUNDLE_SCOPE_UNSUPPORTED')


def resolve_bundle(repo_root: Path, *, source_id: str, sha: str, path: str) -> dict:
    _path(path)
    if not path.startswith('bundles/') or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha):
        raise invalid('A bundle source directory and full commit SHA are required')
    profile, fingerprint = runtime_snapshot()
    entry = detail_at_path(repo_root, path)
    if not entry or entry['kind'] != 'bundle' or entry['document']['bundle_kind'] not in {'VM', 'GROUP'}:
        raise invalid('Only VM bundles or static group bundles bound to one VM can be attached', 'BUNDLE_SCOPE_UNSUPPORTED')
    base = repo_root / path
    kind = entry['document']['bundle_kind']
    if kind == 'GROUP':
        _single_vm_group_binding(base, entry['document']['entrypoint'])
    installed = Path(profile['environment']['RANGE42_BUNDLE_DIR']) / path.removeprefix('bundles/')
    try:
        source_digest = tree_digest(base, max_files=512, max_bytes=16 * 1024 * 1024)
        if source_digest != tree_digest(installed, max_files=512, max_bytes=16 * 1024 * 1024):
            raise ValueError('bundle content differs')
    except (OSError, ValueError):
        raise invalid('Selected bundle content does not match the installed runtime release', 'BUNDLE_CONTENT_MISMATCH') from None
    _check_bundle_scope(base, profile)
    params = entry['document']['params']
    if kind == 'GROUP' and any(param['name'].lower().startswith('ansible_') for param in params):
        raise invalid('Single-VM group bundle descriptors cannot expose Ansible connection overrides', 'BUNDLE_SCOPE_UNSUPPORTED')
    if len(params) > 128 or len({p['name'] for p in params}) != len(params):
        raise invalid('Bundle parameter names must be unique and bounded')
    targets = [param['name'] for param in params if param['name'] in _TARGETS]
    # A play may use a target without a descriptor, but cannot expose it as a
    # free-form caller parameter. Bind every recognized target it actually uses.
    main_text = (base / Path(entry['document']['entrypoint']).name).read_text()
    for name in _TARGETS:
        if name not in targets and re.search(r'{{\s*' + name + r'\s*}}', main_text):
            targets.append(name)
    result = {'source_id': source_id, 'source_sha': sha, 'path': path,
              'entrypoint': entry['document']['entrypoint'].removeprefix('bundles/'),
              'bundle_kind': kind, 'target_kind': 'VM', 'params': params, 'target_vars': targets,
              'proof_kind': 'content_match', 'runtime': {'fingerprint': fingerprint, 'dependencies': dependencies(profile)}}
    claims = {'purpose': 'range42-bundle-resolution-v1', 'resolution': result}
    result['runtime']['proof'] = credential_cipher().encrypt(canonical(claims)).decode()
    return result


def _verify_resolution(resolution: dict, fingerprint: str) -> None:
    try:
        unsigned = copy.deepcopy(resolution)
        token = unsigned['runtime'].pop('proof')
        if not isinstance(token, str) or len(token) > 65536 or base64.urlsafe_b64encode(base64.urlsafe_b64decode(token)).decode() != token:
            raise ValueError('invalid token encoding')
        claims = json.loads(credential_cipher().decrypt(token.encode()))
        if claims != {'purpose': 'range42-bundle-resolution-v1', 'resolution': unsigned}:
            raise ValueError('resolution changed')
        kind = unsigned['bundle_kind']
        if (unsigned['runtime']['fingerprint'] != fingerprint or kind not in {'VM', 'GROUP'}
                or kind == 'GROUP' and (unsigned.get('target_kind') != 'VM' or 'target_group' not in unsigned['target_vars'])):
            raise ValueError('runtime changed')
    except (ValueError, TypeError, KeyError, InvalidToken):
        raise invalid('Bundle resolution proof is invalid or its runtime changed; resolve the attachment again') from None


def _parameters(resolution: dict, values: dict) -> None:
    if not isinstance(values, dict) or len(canonical(values)) > 65536:
        raise invalid('Bundle parameters must be a bounded named object')
    declarations = {param['name']: param for param in resolution['params']}
    for name, value in values.items():
        param = declarations.get(name)
        if not param or param.get('from_vault') or param.get('target') or name in resolution['target_vars'] or name.lower().startswith('ansible_'):
            raise invalid(f'Bundle parameter is undeclared or managed by the runtime: {name}')
        kind = param.get('type', 'string')
        valid = {'string': isinstance(value, str), 'str': isinstance(value, str), 'int': type(value) is int,
                 'bool': type(value) is bool, 'list': isinstance(value, list), 'dict': isinstance(value, dict)}
        if kind == 'bool' and param.get('bool_style') == 'yesno':
            valid['bool'] = type(value) is str and value in {'YES', 'NO'}
        if not valid.get(kind, False) or any(marker in json.dumps(value) for marker in ('{{', '{%', '{#')):
            raise invalid(f'Bundle parameter has an invalid type or template expression: {name}')
        allowed = param.get('allowed', param.get('enum'))
        if isinstance(allowed, list) and value not in allowed:
            raise invalid(f'Bundle parameter must use a declared choice: {name}')
    for name, param in declarations.items():
        if (param.get('required') and not param.get('from_vault') and not param.get('target')
                and not name.lower().startswith('ansible_')
                and name not in resolution['target_vars'] and name not in values and 'default' not in param
                and param.get('default_where', 'none') == 'none'):
            raise invalid(f'Required bundle parameter is missing: {name}')


def _inventory_hosts(value: dict) -> dict:
    hosts = {}
    for node in _walk(value):
        if isinstance(node.get('hosts'), dict):
            for name, settings in node['hosts'].items():
                if name in hosts:
                    raise invalid('Bundle targets require unique inventory host names')
                hosts[name] = settings
    return hosts


def _inventory_groups(value: dict) -> set[str]:
    names = set(value)
    for node in _walk(value):
        if isinstance(node.get('children'), dict):
            names.update(node['children'])
    return names


def validate_scenario_bundles(scenario_dir: Path) -> None:
    manifest = scenario_dir / 'manifest/scenario_bundles.json'
    configure = scenario_dir / 'configure.yml'
    plays = _bounded_document(configure, scenario_dir, yaml_document=True) if configure.exists() else []
    imports = [play for play in plays if isinstance(play, dict) and 'RANGE42_BUNDLE_DIR' in str(
        play.get('ansible.builtin.import_playbook', play.get('import_playbook', '')))] if isinstance(plays, list) else []
    if not manifest.exists() and not manifest.is_symlink():
        if imports:
            raise invalid('Bundle imports require manifest/scenario_bundles.json with verified resolutions')
        return
    document = _bounded_document(manifest, scenario_dir)
    if (not isinstance(document, dict) or set(document) != {'version', 'attachments'} or type(document['version']) is not int or document['version'] != 1
            or not isinstance(document['attachments'], list) or len(document['attachments']) > 64):
        raise invalid('Bundle manifest requires version 1 and at most 64 attachments')
    if not document['attachments']:
        if imports:
            raise invalid('Bundle imports are not declared by the attachment manifest')
        return
    _, fingerprint = runtime_snapshot()
    vms = _bounded_document(scenario_dir / 'manifest/scenario_vms.json', scenario_dir)['vms']
    inventory_document = _bounded_document(scenario_dir / 'hosts.yml', scenario_dir, yaml_document=True)
    inventory = _inventory_hosts(inventory_document)
    groups = _inventory_groups(inventory_document)
    expected = []
    seen = set()
    for attachment in document['attachments']:
        if not isinstance(attachment, dict) or set(attachment) != {'vm_id', 'inventory_host', 'resolution', 'parameters'}:
            raise invalid('Bundle attachment has unexpected or missing fields')
        resolution = attachment['resolution']
        _verify_resolution(resolution, fingerprint)
        _parameters(resolution, attachment['parameters'])
        vm = next((vm for vm in vms if type(attachment['vm_id']) is int and vm.get('vm_id') == attachment['vm_id']), None)
        host = attachment['inventory_host']
        if not vm or host != vm.get('vm_name') or not isinstance(inventory.get(host), dict) or inventory[host].get('ansible_host') != vm.get('ip'):
            raise invalid('Bundle target must match its declared VM ID, inventory hostname and IP')
        if (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,62}', host)
                or host in groups | {'all', 'ungrouped', 'localhost', 'proxmox', 'proxmox_cli', 'scenario_guests', 'r42-proxmox', 'r42-proxmox-cli'}):
            raise invalid('Bundle targets must be literal inventory hostnames distinct from reserved names and inventory groups')
        identity = (attachment['vm_id'], resolution['path'])
        if identity in seen:
            raise invalid('Duplicate bundle attachment for the same VM and bundle')
        seen.add(identity)
        target_values = {'global_vm_ssh_name': host, 'target_ansible_host': host, 'global_vm_ci_ip': vm['ip'], 'target_group': host}
        variables = {**attachment['parameters'], **{name: target_values[name] for name in resolution['target_vars']}}
        expected.append({'path': _BUNDLE_PREFIX + resolution['entrypoint'], 'vars': variables})
    if any(set(play) not in ({'ansible.builtin.import_playbook', 'vars'}, {'import_playbook', 'vars'}) for play in imports):
        raise invalid('Bundle imports may contain only their verified path and variables')
    actual = [{'path': play.get('ansible.builtin.import_playbook', play.get('import_playbook')), 'vars': play.get('vars', {})} for play in imports]
    if actual != expected:
        raise invalid('Bundle configure imports, order or parameters do not match the verified attachment manifest')
