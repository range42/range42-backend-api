"""Reusable template copies have their own canonical deployment and no guest NICs."""
import copy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.core.test_native_scenarios import scenario


def service():
    assert importlib.util.find_spec('app.core.workshop_foundations'), 'Missing managed workshop foundation capability'
    return importlib.import_module('app.core.workshop_foundations')


def context(**changes):
    from app.core.workshop_profiles import WorkshopProfile
    profile = WorkshopProfile.model_validate({
        'version': 1, 'id': 'workshop-fixture', 'vmid_ranges': [[45000, 45039]],
        'proxmox_template': 9240, 'deployer_template': 9241, 'storage': 'ceph-range',
        'networks': [{'bridge': 'vmbr600', 'subnet': '10.78.0.0/28', 'gateway': '10.78.0.1'}]})
    return SimpleNamespace(id='env-'+'a'*24, workshop_profile=profile,
        target_host_id='target', target_node='node-b', target_api_url='https://pve.example.test:8006',
        registration_issue='', **changes)


def specification(**changes):
    return {'namespace': 'workshop-base', 'source_node': 'node-a',
        'proxmox_source': 9230, 'deployer_source': 9222, **changes}


def test_foundation_plan_owns_exact_profile_template_ids_without_borrowing_run_networks():
    module = service()
    original = specification()
    plan = module.compile_foundation(module.FoundationSpec.model_validate(original), context(), run_id='b'*16)
    assert original == specification()
    assert plan['kind'] == 'native-workshop-foundation'
    assert plan['environment_id'] == 'env-'+'a'*24
    assert plan['source_node'] == 'node-a' and plan['target_node'] == 'node-b'
    assert plan['storage'] == 'ceph-range'
    assert [row['vm_id'] for row in plan['assignments']] == [9240, 9241]
    assert all(row['nics'] == [] for row in plan['assignments'])
    assert [row['source_vm_id'] for row in plan['templates']] == [9230, 9222]
    assert all(row['owner'].startswith('range42-foundation:'+'b'*16+':') for row in plan['templates'])
    assert plan['capacity']['memory_mib'] == 20480
    assert plan['capacity']['vcpus'] == 6
    assert all(row['vm_id'] not in (9230, 9222) for row in plan['assignments'])
    topology = module.foundation_topology(plan)
    assert {row['vm_id'] for row in topology['vms']} == {9240, 9241}
    assert {row['vm_id'] for row in topology['templates']} == {9230, 9222}
    assert topology['networks'] == []


@pytest.mark.parametrize('changes', [
    {'source_node': 'node; id'}, {'proxmox_source': True}, {'deployer_source': 9230},
    {'namespace': '../bad'}, {'command': 'id'}, {'destination': 100},
])
def test_foundation_input_cannot_supply_commands_destination_ids_or_ambiguous_sources(changes):
    with pytest.raises(ValidationError):
        service().FoundationSpec.model_validate(specification(**changes))


@pytest.mark.parametrize('damage', ['missing_profile', 'changed_context', 'source_is_destination', 'run_id'])
def test_foundation_plan_requires_the_original_bounded_context_and_separate_sources(damage):
    module = service()
    selected = context()
    request = specification()
    run_id = 'b'*16
    if damage == 'missing_profile':
        selected.workshop_profile = None
    if damage == 'changed_context':
        selected.registration_issue = 'Reviewed target changed'
    if damage == 'source_is_destination':
        request['proxmox_source'] = 9240
    if damage == 'run_id':
        run_id = '../outside'
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error):
        module.compile_foundation(module.FoundationSpec.model_validate(request), selected, run_id=run_id)


def test_catalog_and_saved_scenario_discover_the_same_explicit_foundation_capability(tmp_path):
    from app.core.catalog_index import _scenario
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path, 'scenarios/workshop-foundation')
    (base/'manifest/scenario_vms.json').write_text('{"version":2,"vms":[]}')
    declaration = {'version': 1, 'kind': 'native-workshop-foundation', 'title': 'Prepare workshop templates'}
    (base/'manifest/preparation.json').write_text(json.dumps(declaration))
    assert _scenario(base, tmp_path, topology=True)['document']['preparation'] == declaration
    assert inspect_native_scenario(tmp_path, 'scenarios/workshop-foundation')['preparation'] == declaration
    (base/'manifest/preparation.json').write_text(json.dumps({**declaration, 'command': 'id'}))
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error):
        inspect_native_scenario(tmp_path, 'scenarios/workshop-foundation')


def test_foundation_plan_is_recomputed_and_cannot_be_replaced_by_client_fields():
    module = service()
    descriptor = {'preparation': {'version': 1, 'kind': 'native-workshop-foundation', 'title': 'Prepare'}, 'declared_vmids': []}
    native = {'foundation': specification(), 'parameters': {}}
    plan = module.resolve_native_foundation(native, descriptor, context(), run_id='b'*16)
    assert module.resolve_native_foundation({**native, 'foundation_plan': copy.deepcopy(plan)}, descriptor, context(), run_id='b'*16) == plan
    from app.core.errors import Range42Error
    for changed in [
        {**native, 'foundation_plan': {**plan, 'storage': 'foreign'}},
        {**native, 'workshop': {'namespace': 'foreign'}},
        {**native, 'parameters': {'RANGE42_PREPARATION_COMMAND': 'id'}},
    ]:
        with pytest.raises(Range42Error):
            module.resolve_native_foundation(changed, descriptor, context(), run_id='b'*16)
