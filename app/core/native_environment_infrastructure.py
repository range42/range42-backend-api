"""Observe only generation-bound infrastructure NICs on approved participant bridges."""
import re

import httpx
from pydantic import ValidationError

from app.core.errors import Range42Error
from app.core.proxmox_read import ProxmoxReadError, read_proxmox_data
from app.core.proxmox_tls import proxmox_verify
from app.core.workshop_profiles import InfrastructureGuest


def refused():
    return Range42Error(status=409, code='NATIVE_ENVIRONMENT_INFRASTRUCTURE_CHANGED',
        message='Infrastructure ownership, generation or participant NICs are unavailable or differ between current and pending configuration. Review the original managed gateway before binding it.')


def snapshot(config, *, vmid, node, bridges):
    if not isinstance(config, dict) or config.get('template', 0) not in (0, '0', False) or config.get('lock'):
        raise refused()
    nics = []
    for name, value in config.items():
        if not re.fullmatch(r'net(?:[0-9]|[12][0-9]|3[01])', name):
            continue
        if not isinstance(value, str):
            raise refused()
        fields = value.split(',')
        pairs = [part.split('=', 1) for part in fields]
        if any(len(pair) != 2 for pair in pairs) or len({pair[0] for pair in pairs}) != len(pairs):
            raise refused()
        attrs = dict(pairs)
        if attrs.get('bridge') not in bridges:
            continue
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', pairs[0][1]):
            raise refused()
        nics.append({'interface': name, 'bridge': attrs['bridge'], 'mac': pairs[0][1].lower()})
    nics.sort(key=lambda nic: int(nic['interface'][3:]))
    try:
        return InfrastructureGuest(vmid=vmid, node=node, vmgenid=config.get('vmgenid'),
            owner=config.get('description'), nics=nics)
    except (ValidationError, ValueError, TypeError):
        raise refused() from None


async def observe(host, vmids, bridges):
    if not vmids or len(vmids) > 32 or len(set(vmids)) != len(vmids):
        raise refused()
    result = []
    try:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10.0) as client:
            for vmid in sorted(vmids):
                current = await read_proxmox_data(client, host,
                    f'/nodes/{host.node_name}/qemu/{vmid}/config', params={'current': 1})
                pending = await read_proxmox_data(client, host,
                    f'/nodes/{host.node_name}/qemu/{vmid}/config', params={'current': 0})
                current = snapshot(current, vmid=vmid, node=host.node_name, bridges=bridges)
                pending = snapshot(pending, vmid=vmid, node=host.node_name, bridges=bridges)
                if current != pending:
                    raise refused()
                result.append(current)
    except ProxmoxReadError:
        raise Range42Error(status=502, code='NATIVE_ENVIRONMENT_INFRASTRUCTURE_UNAVAILABLE',
            message='Cannot read the selected infrastructure on this target. Check verified TLS, connectivity and VM audit permission.') from None
    return result


def canonical(guests):
    return [(guest.vmid, guest.node, guest.vmgenid, guest.owner,
        sorted((nic.interface, nic.bridge, nic.mac) for nic in guest.nics)) for guest in sorted(guests, key=lambda item: item.vmid)]
