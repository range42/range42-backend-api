"""Observe native workshop prerequisites without changing Proxmox or its SDN."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import stat

import httpx

from app.core.errors import Range42Error
from app.core.preflight import PreflightCheck
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data, read_proxmox_data
from app.core.proxmox_tls import proxmox_verify
from app.core.workshop_runs import validate_activation
from app.core.host_capacity import normalize_node_capacity


def receipt(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError('Unprotected generation receipt')
        return json.loads(os.read(descriptor, 4097))
    finally:
        os.close(descriptor)


async def workshop_checks(plan: dict, host, *, scope: str, receipt_root: Path | None = None,
                          seat: int = 0, client=None) -> list[PreflightCheck]:
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8, follow_redirects=False) as owned:
            return await workshop_checks(plan, host, scope=scope, receipt_root=receipt_root, seat=seat, client=owned)
    checks = []
    def result(name, ok, detail):
        checks.append(PreflightCheck(check='workshop_' + name, result='pass' if ok else 'block', detail=detail))
    try:
        if scope not in {'teardown', 'delete_vms', 'delete_networks'}:
            validate_activation(plan)
        if host is None or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', host.node_name):
            raise ValueError('Unavailable target')
        selected = [vm for vm in plan['guests'] if seat in (0, vm['seat'])]
        if not selected:
            raise ValueError('Unavailable participant')
        node = '/nodes/' + host.node_name
        async with asyncio.timeout(30):
            permissions = await read_proxmox_data(client, host, '/access/permissions', params={'path': '/vms'})
            if (not isinstance(permissions, dict) or not isinstance(permissions.get('/vms'), dict)
                    or permissions['/vms'].get('VM.Audit') != 1):
                raise ValueError('Complete guest inventory visibility is required')
            rows = await list_proxmox_data(client, host, '/cluster/resources', params={'type': 'vm'})
            if len(rows) > 4096:
                raise ValueError('Inventory exceeds bounds')
            indexed = {int(vm['vmid']): vm for vm in rows}
            for vm in selected:
                current = indexed.get(vm['vm_id'])
                if scope in {'full', 'deploy_vms'}:
                    if current:
                        result('ownership', False, f"VMID {vm['vm_id']} already exists. Use participant recovery for a recorded partial deployment.")
                        return checks
                    free = await read_proxmox_data(client, host, '/cluster/nextid', params={'vmid': vm['vm_id']})
                    if str(free) != str(vm['vm_id']):
                        raise ValueError('Availability assertion differs')
                else:
                    if receipt_root is None:
                        raise ValueError('Missing ownership receipt')
                    try:
                        proof = receipt(receipt_root / f"{vm['vm_id']}.json")
                    except FileNotFoundError:
                        # A clone may never have been attempted in a partial run.
                        # This grants no authority over any live resource.
                        if current or scope not in {'teardown', 'reset'}:
                            raise ValueError('Missing original generation') from None
                        free = await read_proxmox_data(client, host, '/cluster/nextid', params={'vmid': vm['vm_id']})
                        if str(free) != str(vm['vm_id']):
                            raise ValueError('Resource absence differs')
                        continue
                    if proof.get('owner') != vm['owner'] or proof.get('vmid') != vm['vm_id']:
                        raise ValueError('Ownership receipt differs')
                    if current:
                        config = await read_proxmox_data(client, host, f"{node}/qemu/{vm['vm_id']}/config")
                        if (current.get('node') != host.node_name or current.get('type') != 'qemu'
                                or config.get('description') != vm['owner'] or config.get('name') != vm['vm_name']
                                or config.get('vmgenid') != proof.get('vmgenid') or config.get('template')):
                            raise ValueError('Live generation differs')
                    elif proof.get('phase') not in {'retiring', 'retired'} or scope not in {'teardown', 'reset'}:
                        raise ValueError('Missing original generation')
                    else:
                        free = await read_proxmox_data(client, host, '/cluster/nextid', params={'vmid': vm['vm_id']})
                        if str(free) != str(vm['vm_id']):
                            raise ValueError('Resource absence differs')
            result('ownership', True, 'Selected VMIDs are absent for creation, or match their recorded run and clone generation for recovery.')
            if scope in {'teardown', 'delete_vms', 'delete_networks'}:
                return checks  # Missing preparation must never obstruct owned cleanup.
            templates = {}
            for template in (plan['proxmox_template'], plan['deployer_template']):
                templates[template] = await read_proxmox_data(client, host, f'{node}/qemu/{template}/config')
            nested = templates[plan['proxmox_template']]
            template_ok = all(config.get('template') == 1 and 'cloudinit' in config.get('ide2', '')
                and bool(re.search(r'(?:^|,)size=32G(?:,|$)', config.get('scsi0', '')))
                and not any(re.fullmatch(r'(?:hostpci|usb|unused)[0-9]+|args|hookscript|cicustom|net[1-9][0-9]*', key) for key in config)
                and not config.get('lock') and str(config.get('agent', '')).split(',')[0] in {'1', 'enabled=1'}
                for config in templates.values())
            template_ok = template_ok and nested.get('cpu') == 'host' and bool(re.search(r'(?:^|,)size=128G(?:,|$)', nested.get('scsi1', '')))
            result('templates', bool(template_ok), 'Both source templates must be unlocked cloud-init templates with an enabled guest agent; nested Proxmox requires host CPU and its separate 128 GiB lab disk.')
            bridges = await list_proxmox_data(client, host, node + '/network')
            by_name = {row['iface']: row for row in bridges if isinstance(row.get('iface'), str)}
            expected = {vm['bridge'] for vm in selected}
            missing = [name for name in sorted(expected) if name not in by_name or by_name[name].get('type') != 'bridge'
                or not by_name[name].get('active') or by_name[name].get('bridge_ports')]
            result('networks', not missing, 'Required isolated bridges are active without physical bridge ports. Gateway, DNS and participant routing require separate access validation.'
                   if not missing else 'Prepare these isolated participant bridges before deploying: ' + ', '.join(missing))
            node_state = await read_proxmox_data(client, host, node + '/status')
            storage = await read_proxmox_data(client, host, f"{node}/storage/{plan['storage']}/status")
            needed = len(selected) // 2
            _, memory, _ = normalize_node_capacity(node_state)
            if None in (memory.total_bytes, memory.free_bytes, memory.used_bytes):
                raise ValueError('Incomplete memory observations')
            resident = [row for row in rows if row.get('node') == host.node_name
                        and not row.get('template') and row.get('status') != 'stopped']
            if any(type(row.get('maxmem')) is not int or row['maxmem'] <= 0 for row in resident):
                raise ValueError('Incomplete configured guest memory')
            guest_ceiling = sum(row['maxmem'] for row in resident)
            guest_usage = sum(row['mem'] for row in resident if type(row.get('mem')) is int and row['mem'] >= 0)
            host_usage = max(0, memory.used_bytes - guest_usage)
            capacity_ok = (memory.free_bytes >= needed * 20480 * 1024**2
                and guest_ceiling + host_usage + needed * 20480 * 1024**2 <= memory.total_bytes
                and storage.get('active') == 1 and int(storage['avail']) >= needed * 192 * 1024**3)
            result('capacity', capacity_ok, f'Creation requires {needed * 20} GiB memory and {needed * 192} GiB participant VM storage. Admission checks both free RAM and the configured memory ceilings of existing running guests, including observed host usage.'
                   if scope in {'full', 'deploy_vms'} else 'The selected node and VM storage are reachable; recovery retains the owned allocation.')
            if scope not in {'full', 'deploy_vms'}:
                checks[-1] = PreflightCheck(check='workshop_capacity', result='pass', detail='The selected node and VM storage are reachable; recovery retains the owned allocation.')
    except Range42Error as error:
        result('readiness', False, error.message)
    except (ProxmoxReadError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        result('readiness', False, 'Cannot verify workshop prerequisites or original clone ownership. Check the target registration, API permissions, templates, capacity and retained run receipts.')
    return checks
