"""A workshop's generic ownership and resource plan must not depend on an IdP."""
from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from app.core.errors import Range42Error
from tests.core.test_native_scenarios import scenario


def specification(**changes):
    return {
        'namespace': 'training-a', 'seats': 5, 'expires_at': '2030-10-23T18:00:00Z',
        'vmid_start': 45000, 'network_pool': '10.78.0.0/24', 'bridge_start': 500,
        'proxmox_template': 9230, 'deployer_template': 9222,
        'storage': 'ceph-range',
        **changes,
    }


def test_resource_plan_has_separate_seats_and_immutable_run_ownership():
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    spec = WorkshopSpec.model_validate(specification())
    plan = compile_plan(spec, run_id='a' * 32)
    assert len(plan['participants']) == 5
    assert len(plan['assignments']) == 10
    assert plan['identity_profile'] is None and plan['access_profile'] is None
    assert plan['participants'][0]['username'] == 'user01'
    assert plan['participants'][0]['principal_key'] == 'a' * 32 + ':user01'
    assert plan['participants'][0]['proxmox_address'] == '10.78.0.2'
    assert plan['participants'][1]['proxmox_address'] == '10.78.0.18'
    first = plan['guests'][0]
    assert (first['vm_id'], first['bridge'], first['owner']) == (
        45000, 'vmbr500', 'range42-workshop:' + 'a' * 32 + ':user01:proxmox')
    assert first['vm_name'] == 'training-a-user01-pve'
    assert plan['capacity'] == {'memory_mib': 102400, 'vcpus': 30, 'disk_gib': 960}
    assert all(vm['vm_id'] not in {9230, 9222} for vm in plan['assignments'])
    assert 'password' not in json.dumps(plan)
    other = compile_plan(spec, run_id='b' * 32)
    assert other['guests'][0]['owner'] != first['owner']


@pytest.mark.parametrize('changes', [
    {'seats': True}, {'seats': 11}, {'namespace': '../escape'},
    {'expires_at': '2030-10-23T18:00:00'}, {'network_pool': '10.78.0.1/24'},
    {'network_pool': '10.78.0.0/28'}, {'network_pool': '8.8.8.0/24'},
    {'vmid_start': 9222}, {'proxmox_template': 45000},
    {'deployer_template': 9230}, {'vmid_start': 999999999},
    {'identity_profile': {'admin_password': 'private'}}, {'unknown': 'value'},
])
def test_invalid_or_ambiguous_run_input_is_rejected(changes):
    from app.core.workshop_runs import WorkshopSpec
    with pytest.raises((ValidationError, ValueError)):
        WorkshopSpec.model_validate(specification(**changes))


def test_expiry_blocks_new_activation_but_preserves_the_retirement_plan():
    from app.core.workshop_runs import WorkshopSpec, compile_plan, validate_activation
    spec = WorkshopSpec.model_validate(specification(expires_at='2020-10-23T18:00:00Z'))
    plan = compile_plan(spec, run_id='a' * 32)
    with pytest.raises(Range42Error, match='expired'):
        validate_activation(plan, now=datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert len(plan['assignments']) == 10


def test_new_runs_are_discovered_by_capability_metadata_at_any_catalog_path(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path, 'training/custom-installer')
    (base / 'manifest/scenario_vms.json').write_text('{"version":2,"vms":[]}')
    descriptor = {'version': 1, 'kind': 'native-installation', 'title': 'Native installer workshop',
                  'min_seats': 1, 'max_seats': 10}
    (base / 'manifest/workshop.json').write_text(json.dumps(descriptor))
    result = inspect_native_scenario(tmp_path, 'training/custom-installer')
    assert result['workshop'] == descriptor
    (base / 'manifest/workshop.json').write_text(json.dumps({**descriptor, 'admin_token': 'no'}))
    with pytest.raises(Range42Error, match='workshop'):
        inspect_native_scenario(tmp_path, 'training/custom-installer')


def test_catalog_explains_a_parameterized_workshop_before_resources_are_allocated(tmp_path):
    from app.core.catalog_index import detail_at_path
    base = scenario(tmp_path, 'scenarios/installer-course')
    (base / 'manifest/scenario_vms.json').write_text('{"version":2,"vms":[]}')
    document = {'version': 1, 'kind': 'native-installation', 'title': 'Install Range42', 'min_seats': 1, 'max_seats': 10}
    (base / 'manifest/workshop.json').write_text(json.dumps(document))
    entry = detail_at_path(tmp_path, 'scenarios/installer-course')
    assert entry['name'] == 'Install Range42'
    assert entry['document']['workshop'] == document
    assert entry['document']['vm_count'] == 0  # No fictitious preallocated guests.


def test_identity_binding_uses_issuer_and_subject_and_can_span_independent_runs():
    from app.core.workshop_runs import ParticipantBinding
    binding = ParticipantBinding.model_validate({'username': 'user01',
        'issuer': 'https://identity.example.test/realms/training', 'subject': 'opaque-user-id'})
    assert binding.principal == ('https://identity.example.test/realms/training', 'opaque-user-id')
    with pytest.raises(ValidationError):
        ParticipantBinding.model_validate({'username': 'user01', 'email': 'alice@example.test'})
    with pytest.raises(ValidationError):
        ParticipantBinding.model_validate({'username': 'user01', 'issuer': binding.issuer})


def test_execution_receives_server_owned_plan_without_mutating_selected_context(tmp_path, monkeypatch):
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario
    from tests.core.test_native_contexts import context

    workspace, _, host = context(tmp_path, monkeypatch)
    repo = tmp_path / 'repo'
    scenario(repo)
    plan = compile_plan(WorkshopSpec.model_validate(specification()), run_id='a' * 16)
    descriptor = inspect_native_scenario(repo, 'training/exercise-a')
    before = (workspace / 'inventory/inventory_default.yml').read_bytes()
    run = prepare_native_run(repo, descriptor, resolve_context('lab-demo', host), scope='full',
        features={}, parameters={}, artifact_dir=tmp_path / 'attempt', workshop_plan=plan,
        workshop_credentials={'user01': {'proxmox_password': 'TEST-PRIVATE', 'deployer_password': 'OTHER-PRIVATE'}},
        workshop_seat=1)
    material = json.loads(run.variables_file.read_text())
    assert material['range42_workshop_plan'] == plan
    assert material['range42_workshop_selected_seat'] == 1
    assert material['range42_workshop_participants']['user01']['proxmox_password'] == 'TEST-PRIVATE'
    assert run.variables_file.stat().st_mode & 0o777 == 0o600
    assert 'range42_workshop_plan' in (run.context_dir / 'prepare-vars.json').read_text()
    assert 'TEST-PRIVATE' not in (run.context_dir / 'prepare-vars.json').read_text()
    assert (workspace / 'inventory/inventory_default.yml').read_bytes() == before


def test_other_authors_cannot_reserve_inside_an_exclusive_workshop_network():
    from types import SimpleNamespace
    from app.core.allocation_occupancy import Occupancy
    from app.core.allocation_reservations import _plan
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from app.schemas.v1.allocation import AllocationRequest
    plan = compile_plan(WorkshopSpec.model_validate(specification()), run_id='a' * 16)
    claim = SimpleNamespace(assignments=plan['assignments'])
    host = SimpleNamespace(protected_vmids_override_json=None)
    request = AllocationRequest.model_validate({'project_key': 'other', 'vmid_start': 47000,
        'vmid_end': 47999, 'networks': [{'network_id': 'net', 'bridge': 'vmbr500', 'subnet': '10.81.0.0/24'}],
        'vms': [{'node_id': 'other', 'nics': [{'index': 0, 'network_id': 'net'}]}]})
    with pytest.raises(Range42Error, match='workshop'):
        _plan(request, host, [claim], None, Occupancy(set(), set()))


def test_a_fresh_run_has_fresh_credentials_and_usable_salted_password_hashes():
    import subprocess
    from app.core.workshop_runs import WorkshopSpec, compile_plan, create_credentials
    plan = compile_plan(WorkshopSpec.model_validate(specification()), run_id='a' * 16)
    first, second = create_credentials(plan), create_credentials(plan)
    assert set(first) == {f'user{i:02}' for i in range(1, 6)}
    passwords = [record[key] for material in (first, second) for record in material.values()
                 for key in ('proxmox_password', 'deployer_password')]
    assert len(set(passwords)) == 20
    record = first['user01']
    encoded = record['deployer_password_hash']
    assert encoded.startswith('$6$')
    salt = encoded.split('$')[2]
    checked = subprocess.run(['openssl', 'passwd', '-6', '-salt', salt, '-stdin'],
        input=record['deployer_password'], text=True, capture_output=True, check=True).stdout.strip()
    assert checked == encoded
