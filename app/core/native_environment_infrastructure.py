"""Observe only generation-bound infrastructure NICs on approved participant bridges."""
import re
import json
from hashlib import sha256

import httpx
from pydantic import Field, ValidationError, model_validator

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


class GatewayFoundation(InfrastructureGuest):
    name: str = Field(min_length=1, max_length=128)
    configuration_revision: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def unique_ports(self):
        if (len({nic.mac for nic in self.nics}) != len(self.nics)
                or len({nic.bridge for nic in self.nics}) != len(self.nics)
                or any(not char.isprintable() for char in self.name)):
            raise ValueError('Gateway ports need distinct MAC addresses and bridges')
        return self


def foundation_snapshot(config, *, vmid, node):
    try:
        if not isinstance(config, dict):
            raise ValueError()
        bridges = set()
        for name, value in config.items():
            if not re.fullmatch(r'net[0-9]+', name):
                continue
            if not re.fullmatch(r'net(?:[0-9]|[12][0-9]|3[01])', name) or not isinstance(value, str):
                raise ValueError()
            fields = [part.split('=', 1) for part in value.split(',')]
            if any(len(pair) != 2 for pair in fields) or len({pair[0] for pair in fields}) != len(fields):
                raise ValueError()
            bridge = dict(fields).get('bridge', '')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,14}', bridge):
                raise ValueError()
            bridges.add(bridge)
        guest = snapshot(config, vmid=vmid, node=node, bridges=bridges)
        revision = sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return GatewayFoundation(**guest.model_dump(), name=config.get('name'), configuration_revision=revision)
    except (ValueError, TypeError):
        raise refused() from None


def gateway_outside_run(vmid, profile):
    return vmid not in {profile.proxmox_template, profile.deployer_template} and not any(
        start <= vmid <= end for start, end in profile.vmid_ranges)


def gateway_capacity(foundation, networks):
    occupied = len(foundation.nics)
    additional = len({network.bridge for network in networks} - {nic.bridge for nic in foundation.nics})
    if occupied + additional > 32:
        raise Range42Error(status=409, code='NATIVE_ENVIRONMENT_GATEWAY_CAPACITY',
            message=f'The gateway uses {occupied} of 32 NIC slots and needs {additional} additional participant ports. Reduce this environment’s network count or choose another gateway.')
    return {'limit': 32, 'occupied': occupied, 'additional_required': additional,
            'available': 32 - occupied, 'remaining_after_preparation': 32 - occupied - additional}


async def observe_foundation(host, vmid, networks):
    try:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10.0) as client:
            current = await read_proxmox_data(client, host,
                f'/nodes/{host.node_name}/qemu/{vmid}/config', params={'current': 1})
            pending = await read_proxmox_data(client, host,
                f'/nodes/{host.node_name}/qemu/{vmid}/config', params={'current': 0})
        actual = foundation_snapshot(current, vmid=vmid, node=host.node_name)
        if actual != foundation_snapshot(pending, vmid=vmid, node=host.node_name):
            raise refused()
        return {'gateway_foundation': actual, 'nic_capacity': gateway_capacity(actual, networks), 'infrastructure_qualified': False}
    except ProxmoxReadError:
        raise Range42Error(status=502, code='NATIVE_ENVIRONMENT_INFRASTRUCTURE_UNAVAILABLE',
            message='Cannot inspect the selected gateway on this target. Check verified TLS, connectivity and VM audit permission.') from None
