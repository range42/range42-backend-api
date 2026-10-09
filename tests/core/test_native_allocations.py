from types import SimpleNamespace

import httpx
import pytest

from app.core import native_scenarios


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,free,result", [
    ([], True, "pass"),
    ([], False, "block"),
    ([{"vmid": 2001, "name": "unrelated", "type": "qemu", "node": "pve"}], True, "block"),
    ([{"vmid": 2001, "name": "web", "type": "qemu", "node": "pve"}], True, "warn"),
])
async def test_native_live_vmids_are_checked_without_renumbering(rows, free, result):
    assert hasattr(native_scenarios, "check_native_allocations")
    calls = []
    def response(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/cluster/resources'):
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(200 if free else 400, json={"data": '2001'})
    host = SimpleNamespace(api_url='https://pve.test', token_ref='fixture', node_name='pve')
    topology = {"vms": [{"vm_id": 2001, "vm_name": "web"}], "templates": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        checks = await native_scenarios.check_native_allocations(topology, host, client=client)
    assert checks[0].result == result
    assert all(method == 'GET' for method, _ in calls)
