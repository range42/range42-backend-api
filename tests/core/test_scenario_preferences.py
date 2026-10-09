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


@pytest.mark.parametrize('arm,sources', [(False, None), (True, ['203.0.113.7/32']), (False, [])])
def test_native_firewall_preferences_pin_arming_and_exact_networks_without_overriding_inherited_sources(tmp_path, monkeypatch, arm, sources):
    from app.core import runtime_operations
    from app.core.scenario_preferences import storage_runtime_variables
    monkeypatch.setattr(runtime_operations, 'operation_profile', lambda kind: {'contract': 'native-sdn-20260921'})
    value = write_manifest(tmp_path)
    for vm in value['vms']:
        for nic in vm['nics']:
            nic['prefix'] = 23
    (tmp_path / 'manifest/scenario_vms.json').write_text(json.dumps(value))
    (tmp_path / 'manifest/scenario_firewall.json').write_text(json.dumps({
        'version': 1, 'arm_vms': arm, 'prepare_management_access': False, 'ssh_sources': sources,
    }))
    variables = storage_runtime_variables(tmp_path)
    assert variables['FIREWALL_ARM_VMS'] == ('YES' if arm else 'NO')
    assert variables['r42_fw_prepare_management_access'] is False
    assert variables['r42_fw_scenario_networks'] == ['10.42.0.0/23', '10.42.2.0/23']
    if sources is None:
        assert 'range42_fw_vm_ssh_sources' not in variables
    else:
        assert variables['range42_fw_vm_ssh_sources'] == sources


def test_storage_variable_injection_is_rejected_before_runner_setup(tmp_path):
    from app.core.errors import Range42Error
    from app.core.scenario_preferences import storage_runtime_variables
    write_manifest(tmp_path, ('{{ secret }}',))
    with pytest.raises(Range42Error) as error:
        storage_runtime_variables(tmp_path)
    assert error.value.code == 'PROJECT_PREFERENCES_INVALID'
    assert '{{ secret }}' not in str(error.value)


@pytest.mark.parametrize('actual_controller', [False, True])
def test_ansible_cloud_init_preferences_override_vault_and_keep_inheritance_per_vm(tmp_path, actual_controller):
    from app.core.scenario_preferences import storage_runtime_variables
    controller_path = os.getenv('R42_PREFERENCE_CLOUDINIT_TASK')
    if actual_controller and not controller_path:
        pytest.skip('Set R42_PREFERENCE_CLOUDINIT_TASK to the reviewed controller cloudinit_set_variables.yaml')
    value = write_manifest(tmp_path)
    value['guest_preferences_version'] = 2
    preferences = [
        {'ssh_user': 'operator', 'dns_servers': ['10.42.1.2', '1.1.1.1'], 'dns_search_domain': 'lab.example'},
        {'ssh_user': 'second', 'dns_servers': None, 'dns_search_domain': None},
        {'ssh_user': 'third', 'dns_servers': ['9.9.9.9'], 'dns_search_domain': 'other.example'},
    ]
    for vm, cloud_init in zip(value['vms'], preferences, strict=True):
        vm['cloud_init'] = cloud_init
    (tmp_path / 'manifest/scenario_vms.json').write_text(json.dumps(value))
    variables = storage_runtime_variables(tmp_path)
    assert variables['r42_guest_cloud_init'] == {str(vm['vm_id']): vm['cloud_init'] for vm in value['vms']}
    assert all('password' not in key and 'ssh_key' not in key for key in variables)
    body = {'ciuser': '{{ vm_ci_user | default(omit) }}', 'nameserver': '{{ vm_ci_dns_ips | default(omit) }}',
            'searchdomain': '{{ vm_ci_dns_domain | default(omit) }}'}
    body_variables = {}
    if actual_controller:
        source = yaml.safe_load(Path(controller_path).read_text())
        tasks = [nested for task in source for nested in task.get('block', [task])]
        injection = next(task for task in tasks if task.get('uri', {}).get('method') == 'PUT')
        body = injection['uri']['body']
        body_variables = {'_vm_bootstrap_extra_config': {}, **injection.get('vars', {})}
    conflicting = {key: 'conflicting-vault' for key in ('vm_ci_user', 'default_admin_vm_ci_user', 'vm_ci_dns_ips', 'global_vm_ci_dns_ips', 'vm_ci_dns_domain')}
    (tmp_path / 'vault.yml').write_text(yaml.safe_dump(conflicting))
    (tmp_path / 'bootstrap.yml').write_text(yaml.safe_dump([{
        'hosts': 'localhost', 'gather_facts': False, 'vars_files': ['vault.yml'],
        'tasks': [{'ansible.builtin.set_fact': {'captured_body': body}, 'vars': body_variables},
                  {'ansible.builtin.assert': {'that': [
                      "captured_body['ciuser'] == expected.ssh_user",
                      "captured_body.get('nameserver') == (expected.dns_servers | join(' ') if expected.dns_servers is not none else none)",
                      "captured_body.get('searchdomain') == expected.dns_search_domain",
                      "('nameserver' in captured_body) == (expected.dns_servers is not none)",
                      "('searchdomain' in captured_body) == (expected.dns_search_domain is not none)",
                      "default_admin_vm_ci_user == expected.ssh_user",
                  ]}}],
    }]))
    (tmp_path / 'main.yml').write_text(yaml.safe_dump([{
        'ansible.builtin.import_playbook': 'bootstrap.yml',
        'vars': {'global_vm_id': vm['vm_id'], 'expected': vm['cloud_init'], 'vm_ci_ssh_key': 'ssh-ed25519 local-fixture', **conflicting},
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
