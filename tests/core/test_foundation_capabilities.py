"""Optional foundation capability does not grant generic guest execution."""
import copy

import pytest
from pydantic import ValidationError

from app.core.physical_nodes import NodeAttestation
from app.core.workshop_foundations import FoundationDescriptor

CAPABILITIES = ['inspect', 'capacity', 'capacity-admit', 'capacity-status',
                'foundation-profile', 'foundation-prepare', 'foundation-status']


def attestation():
    return {'protocol': 'range42-preparation-agent-v1', 'physical_node_id': 'a'*32,
        'hardware_binding': 'b'*64, 'node_name': 'pve-test', 'installation_id': 'c'*32,
        'configuration_revision': 'd'*64, 'api_certificate_sha256': 'e'*64,
        'node_certificate_sha256': 'f'*64, 'boot_id': '11111111-2222-4333-8444-555555555555',
        'supported_operations': copy.copy(CAPABILITIES), 'execution_ready': False, 'infrastructure_qualified': False}


def test_optional_foundation_attestation_is_an_exact_capability_sequence():
    assert NodeAttestation.model_validate(attestation()).supported_operations == CAPABILITIES
    for sequence in [CAPABILITIES[::-1], CAPABILITIES[:-1], CAPABILITIES+['shell'], ['foundation-prepare']]:
        value = attestation()
        value['supported_operations'] = sequence
        with pytest.raises(ValidationError):
            NodeAttestation.model_validate(value)


@pytest.mark.parametrize('version', [True, 1.0, '1'])
def test_foundation_manifest_rejects_ambiguous_protocol_versions(version):
    with pytest.raises(ValidationError):
        FoundationDescriptor.model_validate({'version': version,
            'kind': 'native-workshop-foundation', 'title': 'Reusable workshop templates'})


@pytest.mark.parametrize('field', ['execution_ready', 'infrastructure_qualified'])
def test_foundation_progress_never_accepts_integer_readiness_flags(field):
    from app.core.foundation_execution import FoundationReceipt
    value = {'protocol': 'range42-foundation-receipt-v1', 'request_id': 'a'*32, 'request_digest': 'b'*64,
        'status': 'absent', 'node': attestation(), 'templates': [
            {'vm_id': vmid, 'phase': 'pending', 'vmgenid': None, 'configuration_revision': None, 'volumes': []}
            for vmid in [9240, 9241]], 'foundation_ready': False, 'execution_ready': False, 'infrastructure_qualified': False}
    value[field] = 0
    with pytest.raises(ValidationError):
        FoundationReceipt.model_validate(value)
