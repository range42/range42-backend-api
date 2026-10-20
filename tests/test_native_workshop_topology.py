import json

from app.core.native_topology import scenario_topology


def test_existing_bridge_topology_preserves_subnets_and_conditional_vm_resources(tmp_path):
    base = tmp_path / 'scenarios/install-workshop'
    (base / 'manifest').mkdir(parents=True)
    (base / 'manifest/scenario_networks.json').write_text(json.dumps({
        'mode': 'existing_bridge', 'networks': [
            {'vnet': 'vmbr430', 'subnet': '10.77.32.0/28', 'gateway': '10.77.32.1'},
        ],
    }))
    manifest = {'vms': [{'vm_id': 43010, 'vm_name': 'participant-pve', 'bridge': 'vmbr430',
                         'ip': '10.77.32.2', 'cores': 4, 'memory_mib': 16384, 'disk_gib': 160,
                         'seat': 1, 'group': 'user01', 'requires_features': ['EXTRA_SEATS']}], 'templates': []}
    view = scenario_topology(tmp_path, base, manifest)
    assert view['networks'] == [{'vnet': 'vmbr430', 'subnet': '10.77.32.0/28', 'gateway': '10.77.32.1'}]
    assert view['vms'][0]['requires_features'] == ['EXTRA_SEATS']
    assert view['vms'][0]['cores'] == 4
    assert view['vms'][0]['memory_mib'] == 16384
    assert view['vms'][0]['disk_gib'] == 160

    assert view['vms'][0]['seat'] == 1
