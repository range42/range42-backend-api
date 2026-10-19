import json
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from tests.core.test_workshop_runs import specification


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'bridge', 'template', 'wrong_disks', 'passthrough', 'capacity', 'committed_memory', 'permissions', 'occupied', 'api'])
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
        elif path.endswith('/config'):
            template = 9230 if '/9230/' in path else 9222
            result = {'template': 0 if fault == 'template' else 1, 'cpu': 'host', 'agent': 'enabled=1',
                'scsi0': f'ceph-control:base-{template}-disk-0,size=32G', 'ide2': 'ceph-control:vm-cloudinit,media=cdrom'}
            if template == 9230:
                result['scsi1'] = f'ceph-control:base-{template}-disk-1,size=128G'
            if fault == 'wrong_disks':
                result['scsi0'] = f'ceph-control:base-{template}-disk-0,size=24G'
            if fault == 'passthrough':
                result['hostpci0'] = '0000:01:00.0'
        else:
            raise AssertionError('Unexpected path: ' + path)
        return httpx.Response(200, json={'data': result})
    host = SimpleNamespace(api_url='https://pve.example.test:8006', node_name='node-a', token_ref='test-private')
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        checks = await workshop_checks(plan, host, scope='full', client=client)
    if fault:
        assert any(check.result == 'block' for check in checks)
    else:
        assert not any(check.result == 'block' for check in checks)
        assert {'workshop_templates', 'workshop_networks', 'workshop_capacity', 'workshop_ownership'} <= {check.check for check in checks}
        assert '/nodes/node-a/qemu/9230/config' in seen
        assert '/nodes/node-a/qemu/9222/config' in seen
        assert '/nodes/node-a/network' in seen
    public = json.dumps([asdict(check) for check in checks])
    assert 'test-private' not in public and 'not for the public report' not in public


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
