"""Reviewed storage remains authoritative across per-VM imports and vault defaults."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tests.core.test_scenario_manifest import manifest


def write_manifest(root, pools=('fast-pool', None, 'slow-pool')):
    value = manifest()
    seed = value['vms'][0]
    value['vms'] = []
    for index, pool in enumerate(pools):
        row = json.loads(json.dumps(seed))
        row.update(vm_id=3101 + index, vm_name=f'guest-{index}', storage=pool, ip=f'10.42.1.{10 + index}')
        row['nics'][0]['ip'] = row['ip']
        row['nics'][1]['ip'] = f'10.42.2.{10 + index}'
        value['vms'].append(row)
    value['guest_preferences_version'] = 1
    (root / 'manifest').mkdir()
    (root / 'manifest/scenario_vms.json').write_text(json.dumps(value))
    return value


def test_storage_map_is_manifest_derived_and_legacy_inputs_stay_unchanged(tmp_path):
    from app.core.scenario_preferences import storage_runtime_variables
    value = write_manifest(tmp_path)
    variables = storage_runtime_variables(tmp_path)
    assert variables['r42_guest_storage'] == {'3101': 'fast-pool', '3102': None, '3103': 'slow-pool'}
    assert set(variables) == {'r42_guest_storage', 'proxmox_dest_vm_storage_name'}
    del value['guest_preferences_version']
    for vm in value['vms']:
        del vm['storage']
    (tmp_path / 'manifest/scenario_vms.json').write_text(json.dumps(value))
    assert storage_runtime_variables(tmp_path) == {}


def test_storage_variable_injection_is_rejected_before_runner_setup(tmp_path):
    from app.core.errors import Range42Error
    from app.core.scenario_preferences import storage_runtime_variables
    write_manifest(tmp_path, ('{{ secret }}',))
    with pytest.raises(Range42Error) as error:
        storage_runtime_variables(tmp_path)
    assert error.value.code == 'PROJECT_PREFERENCES_INVALID'
    assert '{{ secret }}' not in str(error.value)


@pytest.mark.parametrize('actual_clone', [False, True])
def test_ansible_selected_and_inherited_storage_override_vault_without_leaking_between_vms(tmp_path, actual_clone):
    from app.core.scenario_preferences import storage_runtime_variables
    clone_path = os.getenv('R42_PREFERENCE_CLONE_TASK')
    if actual_clone and not clone_path:
        pytest.skip('Set R42_PREFERENCE_CLONE_TASK to the reviewed controller vm_clone.yaml for its exact body consumer')
    value = write_manifest(tmp_path)
    variables = storage_runtime_variables(tmp_path)
    body = {'storage': '{{ proxmox_dest_vm_storage_name | default(omit) }}'}
    if actual_clone:
        source = yaml.safe_load(Path(clone_path).read_text())
        clone = next(task['uri'] for task in source[0]['block'] if task.get('uri', {}).get('method') == 'POST')
        assert clone['body']['full'] == 1
        body = clone['body']
    (tmp_path / 'vault.yml').write_text('proxmox_dest_vm_storage_name: conflicting-vault-pool\nr42_guest_storage: {3101: conflicting-map}\n')
    # Use the controller's actual URI body as a harmless set_fact module argument.
    # Ansible templates it and removes omit exactly as for a URI body; no HTTP task runs.
    (tmp_path / 'bootstrap.yml').write_text(yaml.safe_dump([{
        'hosts': 'localhost', 'gather_facts': False, 'vars_files': ['vault.yml'],
        'tasks': [{'ansible.builtin.set_fact': {'captured_body': body}},
                  {'ansible.builtin.assert': {'that': [
                      "captured_body.get('storage') == expected_pool",
                      "('storage' in captured_body) == (expected_pool is not none)",
                  ]}}],
    }]))
    (tmp_path / 'main.yml').write_text(yaml.safe_dump([{
        'ansible.builtin.import_playbook': 'bootstrap.yml',
        'vars': {'global_vm_id': vm['vm_id'], 'vm_id': 9901, 'vm_new_id': vm['vm_id'],
                 'vm_name': vm['vm_name'], 'vm_description': 'test', 'expected_pool': vm['storage'],
                 'proxmox_dest_vm_storage_name': 'conflicting-import-pool'},
    } for vm in value['vms']]))
    (tmp_path / 'extras.yml').write_text(yaml.safe_dump(variables))
    (tmp_path / 'ansible.cfg').write_text('[defaults]\nretry_files_enabled=False\n')
    executable = Path(sys.executable).with_name('ansible-playbook')
    env = {key: val for key, val in os.environ.items() if not key.startswith('ANSIBLE_')}
    env.update(ANSIBLE_CONFIG=str(tmp_path / 'ansible.cfg'), ANSIBLE_NOCOLOR='1')
    result = subprocess.run([str(executable), '-i', 'localhost,', '-c', 'local', '-e', '@extras.yml', 'main.yml'],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'failed=0' in result.stdout
