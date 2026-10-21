import json
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from tests.core.test_workshop_runs import specification


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'sealed', 'extra_nic', 'bridge', 'shared_bridge', 'template', 'wrong_disks', 'passthrough', 'missing_nic', 'malformed_nic', 'api_template_list', 'api_template_text', 'capacity', 'committed_memory', 'permissions', 'occupied', 'api'])
async def test_workshop_readiness_observes_real_target_inputs_and_fails_closed(fault):
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from app.core.workshop_preflight import workshop_checks
    plan = compile_plan(WorkshopSpec.model_validate(specification(seats=1)), run_id='a' * 16)
    seen = []
    def endpoint(request):
        path = request.url.path.removeprefix('/api2/json')
        seen.append(path)
        assert request.method == 'GET'
        if fault == 'api':
            return httpx.Response(403, json={'private': 'not for the public report'})
        if path == '/access/permissions':
            result = {'/vms': {'VM.Audit': 0 if fault == 'permissions' else 1}}
        elif path == '/cluster/resources':
            result = [{'vmid': 45000, 'node': 'node-a', 'type': 'qemu', 'name': 'foreign'}] if fault == 'occupied' else []
            if fault == 'committed_memory':
                result = [{'vmid': 46000, 'node': 'node-a', 'type': 'qemu', 'name': 'idle-existing',
                           'status': 'running', 'maxmem': 60 * 1024**3, 'mem': 1024**3}]
            if fault == 'shared_bridge':
                result = [{'vmid': 46000, 'node': 'node-a', 'type': 'qemu', 'status': 'stopped', 'name': 'unrelated'}]
        elif path == '/cluster/nextid':
            result = request.url.params['vmid']
        elif path == '/nodes/node-a/network':
            result = [] if fault == 'bridge' else [{'iface': 'vmbr500', 'type': 'bridge', 'active': 1, 'bridge_ports': ''}]
        elif path.endswith('/status') and '/storage/' in path:
            result = {'active': 1, 'avail': 10**12}
        elif path == '/nodes/node-a/status':
            total = 64 * 1024**3 if fault == 'committed_memory' else 2**41
            free = 63 * 1024**3 if fault == 'committed_memory' else 1024 if fault == 'capacity' else 2**40
            result = {'memory': {'total': total, 'used': total - free, 'free': free}, 'cpuinfo': {'cpus': 64}, 'cpu': 0.0}
        elif path == '/nodes/node-a/qemu/46000/config':
            bridge = 'vmbr500' if fault == 'shared_bridge' else 'vmbr900'
            result = {'name': 'unrelated', 'net0': f'virtio=AA:BB:CC:DD:EE:FF,bridge={bridge}', 'template': 0}
        elif path.endswith('/config'):
            template = 9230 if '/9230/' in path else 9222
            result = {'template': 0 if fault == 'template' else 1, 'cpu': 'host', 'agent': 'enabled=1',
                'net0': 'virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr422',
                'scsi0': f'ceph-control:base-{template}-disk-0,size=32G', 'ide2': 'ceph-control:vm-cloudinit,media=cdrom'}
            if template == 9230:
                result['scsi1'] = f'ceph-control:base-{template}-disk-1,size=128G'
            if fault == 'wrong_disks':
                result['scsi0'] = f'ceph-control:base-{template}-disk-0,size=24G'
            if fault == 'passthrough':
                result['hostpci0'] = '0000:01:00.0'
            if fault in ('missing_nic', 'sealed'):
                result.pop('net0')
            if fault == 'missing_nic':
                result['ipconfig0'] = 'ip=192.0.2.2/24,gw=192.0.2.1'
            if fault == 'extra_nic':
                result['net1'] = 'virtio=AA:BB:CC:DD:EE:01,bridge=vmbr422'
            elif fault == 'malformed_nic':
                result['net0'] = 'virtio=not-a-mac,bridge=vmbr422'
            if fault == 'api_template_list':
                result = []
            elif fault == 'api_template_text':
                result = 'private-template-upstream-response'
        else:
            raise AssertionError('Unexpected path: ' + path)
        return httpx.Response(200, json={'data': result})
    host = SimpleNamespace(api_url='https://pve.example.test:8006', node_name='node-a', token_ref='test-private')
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        checks = await workshop_checks(plan, host, scope='full', client=client)
    if fault not in (None, 'sealed'):
        assert any(check.result == 'block' for check in checks)
    else:
        assert not any(check.result == 'block' for check in checks)
        assert {'workshop_templates', 'workshop_networks', 'workshop_capacity', 'workshop_ownership'} <= {check.check for check in checks}
        assert '/nodes/node-a/qemu/9230/config' in seen
        assert '/nodes/node-a/qemu/9222/config' in seen
        assert '/nodes/node-a/network' in seen
    public = json.dumps([asdict(check) for check in checks])
    assert 'test-private' not in public and 'not for the public report' not in public and 'private-template-upstream-response' not in public


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['reset', 'teardown', 'configure'])
async def test_cleanup_of_a_partial_run_allows_never_created_guests_only_after_global_absence(tmp_path, scope):
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from app.core.workshop_preflight import workshop_checks
    plan = compile_plan(WorkshopSpec.model_validate(specification(seats=1)), run_id='a' * 16)
    seen = []
    def endpoint(request):
        path = request.url.path.removeprefix('/api2/json')
        seen.append(path)
        if path == '/access/permissions':
            data = {'/vms': {'VM.Audit': 1}}
        elif path == '/cluster/resources':
            data = []
        elif path == '/cluster/nextid':
            data = request.url.params['vmid']
        else:
            # Ownership must be decided before reset checks its preparation.
            return httpx.Response(503)
        return httpx.Response(200, json={'data': data})
    host = SimpleNamespace(api_url='https://pve.example.test:8006', node_name='node-a', token_ref='test-private')
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        checks = await workshop_checks(plan, host, scope=scope, receipt_root=tmp_path, client=client)
    assert any(check.check == 'workshop_ownership' and check.result == 'pass' for check in checks) == (scope != 'configure')
    assert ('/cluster/nextid' in seen) == (scope != 'configure')


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'owner', 'generation', 'mac', 'pending_nic', 'container'])
async def test_only_exact_approved_infrastructure_may_share_a_participant_bridge(fault):
    from app.core.workshop_preflight import _network_attachments
    from app.core.workshop_profiles import WorkshopProfile
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    plan = compile_plan(WorkshopSpec.model_validate(specification(seats=1)), run_id='a' * 16)
    infrastructure = {'vmid': 42100, 'node': 'node-a', 'owner': 'platform:gateway',
        'vmgenid': '12345678-1234-1234-1234-123456789abc',
        'nics': [{'interface': 'net14', 'bridge': 'vmbr500', 'mac': 'bc:24:11:60:00:01'}]}
    profile = WorkshopProfile.model_validate({'version': 1, 'id': 'approved-workshops',
        'vmid_ranges': [[45000, 45039]], 'proxmox_template': 9230, 'deployer_template': 9222,
        'storage': 'ceph-range', 'networks': [{'bridge': 'vmbr500', 'subnet': '10.78.0.0/28', 'gateway': '10.78.0.1'}],
        'infrastructure_guests': [infrastructure]})
    views = []
    def endpoint(request):
        view = int(request.url.params['current'])
        views.append(view)
        config = {'description': infrastructure['owner'], 'vmgenid': infrastructure['vmgenid'],
            'net14': 'virtio=BC:24:11:60:00:01,bridge=vmbr500'}
        if fault == 'owner':
            config['description'] = 'foreign'
        elif fault == 'generation':
            config['vmgenid'] = 'abcdefab-1234-1234-1234-123456789abc'
        elif fault == 'mac':
            config['net14'] = 'virtio=BC:24:11:60:00:02,bridge=vmbr500'
        elif fault == 'pending_nic' and view == 0:
            config['net15'] = 'virtio=BC:24:11:60:00:01,bridge=vmbr500'
        return httpx.Response(200, json={'data': config})
    host = SimpleNamespace(api_url='https://pve.example.test:8006', node_name='node-a', token_ref='synthetic')
    rows = [{'vmid': 42100, 'node': 'node-a', 'type': 'lxc' if fault == 'container' else 'qemu', 'status': 'running'}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        if fault:
            with pytest.raises(ValueError, match='unapproved'):
                await _network_attachments(client, host, rows, plan['guests'], {}, profile)
        else:
            await _network_attachments(client, host, rows, plan['guests'], {}, profile)
            assert views == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'running', 'complete', 'unrecorded', 'extra_nic', 'pending_ip', 'generation'])
async def test_recorded_sealed_clone_can_resume_without_adopting_network_changes(fault):
    from app.core.workshop_preflight import _network_attachments
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    plan = compile_plan(WorkshopSpec.model_validate(specification(seats=1)), run_id='a' * 16)
    guest = plan['guests'][0]
    generation = '12345678-1234-1234-1234-123456789abc'
    proof = {'vmid': guest['vm_id'], 'owner': guest['owner'], 'vmgenid': generation,
             'bootstrap_network': {'net0': None, 'ipconfig0': None}}
    if fault == 'complete':
        proof['bootstrap_complete'] = True
    if fault == 'unrecorded':
        proof.pop('bootstrap_network')
    seen = []
    def endpoint(request):
        current = int(request.url.params['current'])
        seen.append(current)
        config = {'description': guest['owner'], 'vmgenid': generation}
        if fault == 'extra_nic':
            config['net1'] = 'virtio=AA:BB:CC:DD:EE:01,bridge=vmbr500'
        if fault == 'pending_ip' and current == 0:
            config['ipconfig0'] = 'ip=192.0.2.2/24'
        if fault == 'generation':
            config['vmgenid'] = 'abcdefab-1234-1234-1234-123456789abc'
        return httpx.Response(200, json={'data': config})
    host = SimpleNamespace(api_url='https://pve.example.test:8006', node_name='node-a', token_ref='synthetic')
    rows = [{'vmid': guest['vm_id'], 'node': 'node-a', 'type': 'qemu',
             'status': 'running' if fault == 'running' else 'stopped'}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        if fault:
            with pytest.raises(ValueError):
                await _network_attachments(client, host, rows, plan['guests'], {guest['vm_id']: proof}, None)
        else:
            await _network_attachments(client, host, rows, plan['guests'], {guest['vm_id']: proof}, None)
            assert seen == [0, 1]
