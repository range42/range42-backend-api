"""Parse public hardware observations and patch only supported existing NIC keys."""
import hashlib
import re

from app.core.scenario_capacity import _size_bytes
from app.schemas.v1.vm_hardware import DiskView, HardwareValues, NicView

NIC = re.compile(r'net(?:[0-9]|[12][0-9]|3[01])$')
DISK = re.compile(r'(?:ide[0-3]|scsi(?:[0-9]|[12][0-9]|30)|virtio(?:[0-9]|1[0-5])|sata[0-5])$')
MODELS = {'e1000', 'e1000-82540em', 'e1000-82544gc', 'e1000-82545em', 'e1000e', 'i82551', 'i82557b', 'i82559er', 'ne2k_isa', 'ne2k_pci', 'pcnet', 'rtl8139', 'virtio', 'vmxnet3'}


def parts(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError('Unsupported hardware configuration')
    values = {}
    for index, item in enumerate(raw.split(',')):
        if '=' in item:
            key, value = item.split('=', 1)
        elif index == 0:
            key, value = '_first', item
        else:
            raise ValueError('Unsupported hardware configuration')
        if not key or not value or key in values:
            raise ValueError('Ambiguous hardware configuration')
        values[key] = value
    return values


def nic_view(key, raw):
    try:
        values = parts(raw)
        aliases = [key for key in values if key in MODELS]
        if len(aliases) > 1 or aliases and ('model' in values or 'macaddr' in values):
            raise ValueError('Ambiguous NIC identity')
        model = aliases[0] if aliases else values.get('model', values.get('_first'))
        mac = values[aliases[0]] if aliases else values.get('macaddr')
        bridge = values.get('bridge')
        if model not in MODELS or not isinstance(mac, str) or not re.fullmatch(r'(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}', mac):
            raise ValueError('Unsupported NIC identity')
        if bridge is not None and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,14}', bridge):
            raise ValueError('Unsupported bridge')
        if any(values.get(key, '0') not in ('0', '1') for key in ('firewall', 'link_down')):
            raise ValueError('Unsupported NIC flags')
        tag = values.get('tag')
        if tag is not None and (not re.fullmatch(r'[0-9]{1,4}', tag) or not 1 <= int(tag) <= 4094):
            raise ValueError('Unsupported VLAN')
        return NicView(id=key, model=model, mac=mac, bridge=bridge, tag=int(tag) if tag else None,
                       firewall=values.get('firewall') == '1', link_down=values.get('link_down') == '1', editable=True)
    except ValueError:
        return NicView(id=key)


def patch_nic(raw, changes):
    values = parts(raw)
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = str(int(value)) if isinstance(value, bool) else str(value)
    return ','.join(value if key == '_first' else f'{key}={value}' for key, value in values.items())


def disk_view(key, raw):
    try:
        values = parts(raw)
        if values.get('media') == 'cdrom':
            return None
        volume = values.get('_first', values.get('file', ''))
        pool, separator, name = volume.partition(':')
        size = _size_bytes(values.get('size', ''))
        if not separator or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', pool) or not name:
            raise ValueError('Unsupported disk volume')
        return DiskView(id=key, pool=pool, size_bytes=size, editable=size is not None,
                        volume_fingerprint=hashlib.sha256(volume.encode()).hexdigest())
    except ValueError:
        return DiskView(id=key)


def hardware_values(config):
    nics, disks = [], []
    for key in sorted(config, key=lambda key: (re.sub(r'\d+$', '', key), int(re.search(r'\d+$', key)[0]) if re.search(r'\d+$', key) else 0)):
        if NIC.fullmatch(key):
            nics.append(nic_view(key, config[key]))
        elif DISK.fullmatch(key):
            disk = disk_view(key, config[key])
            if disk is not None:
                disks.append(disk)
    return HardwareValues(nics=nics, disks=disks)
