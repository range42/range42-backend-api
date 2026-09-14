"""Reviewed edits of one existing imported QEMU NIC or disk; never guest creation."""
import hmac
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Range42Error
from app.core.locks import ProvisioningLock
from app.core.proxmox_hardware import DISK, NIC, disk_view, hardware_values, nic_view, patch_nic
from app.core.proxmox_tls import proxmox_verify
from app.routes.v1.proxmox._helpers import _assert_snapshot_member_free, _assert_vmid_safe, _auth_headers, _config_target_digest, _config_task_vmid, _get_host, _session
from app.routes.v1.proxmox.vm_config import _claims, _digest, _error, _read_configs, _url
from app.schemas.v1.vm_hardware import DiskGrowth, HardwareResult, HardwareReview, NicUpdate

router = APIRouter()
VmId = Annotated[int, Path(ge=100, le=999999999)]
NicId = Annotated[str, Path(pattern=r'^net(?:[0-9]|[12][0-9]|3[01])$')]
DiskId = Annotated[str, Path(pattern=r'^(?:ide[0-3]|scsi(?:[0-9]|[12][0-9]|30)|virtio(?:[0-9]|1[0-5])|sata[0-5])$')]


async def _review(cli, row, vmid):
    configured, current = await _read_configs(cli, row, vmid, 'qemu')
    keys = {key for key in configured.keys() | current.keys() if NIC.fullmatch(key) or DISK.fullmatch(key)}
    return HardwareReview(host_id=row.id, node=row.node_name, vmid=vmid,
        digest=_digest(row, vmid, 'qemu', configured['digest']), target_digest=_config_target_digest(row, vmid, 'qemu'),
        current=hardware_values(current), configured=hardware_values(configured),
        pending=sorted(key for key in keys if configured.get(key) != current.get(key))), configured


async def _read_data(cli, row, suffix, params=None):
    try:
        url = f"{row.api_url.rstrip('/')}/api2/json/nodes/{quote(row.node_name, safe='')}/{suffix}"
        response = await cli.get(url, headers=_auth_headers(row), params=params)
        if response.status_code != 200:
            raise ValueError()
        return response.json()['data']
    except (httpx.RequestError, ValueError, KeyError):
        raise _error('VM_HARDWARE_UNAVAILABLE', 'The selected host resource could not be verified. No hardware edit was sent.') from None


@router.get('/hosts/{host_id}/vms/{vmid}/hardware/review', response_model=HardwareReview)
async def review_hardware(host_id: str, vmid: VmId, session: AsyncSession = Depends(_session)):
    row = await _get_host(host_id, session)
    _assert_vmid_safe(row, vmid, 'configure')
    await _claims(session, vmid)
    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10) as cli:
        result, _ = await _review(cli, row, vmid)
    return result


async def _dispatch(cli, row, vmid, suffix, form, kind, expected):
    url = _url(row, vmid, 'qemu') if kind == 'qmconfig' else _url(row, vmid, 'qemu').removesuffix('/config') + '/resize'
    try:
        response = await (cli.post if kind == 'qmconfig' else cli.put)(url, headers=_auth_headers(row), data=form)
    except httpx.RequestError:
        return HardwareResult(status='unconfirmed', reason='write_outcome_unknown')
    if response.status_code in (401, 403):
        raise _error('VM_CONFIG_FORBIDDEN', 'Proxmox denied this hardware edit.', 403)
    if response.status_code != 200:
        return HardwareResult(status='unconfirmed', reason='write_outcome_unknown')
    try:
        value = response.json()['data']
    except (ValueError, KeyError, TypeError):
        return HardwareResult(status='unconfirmed', reason='unexpected_write_response')
    if value is not None:
        if _config_task_vmid(value, row.node_name, kinds=(kind,)) == vmid:
            return HardwareResult(status='accepted', upid=value)
        return HardwareResult(status='unconfirmed', reason='unexpected_write_response')
    try:
        after, configured = await _review(cli, row, vmid)
    except Range42Error:
        return HardwareResult(status='unconfirmed', reason='readback_unavailable')
    actual = nic_view(suffix, configured.get(suffix)) if kind == 'qmconfig' else disk_view(suffix, configured.get(suffix))
    if actual != expected:
        return HardwareResult(status='unconfirmed', review=after, reason='readback_mismatch')
    return HardwareResult(status='configured', review=after)


async def _update(host_id, vmid, key, body, session):
    with ProvisioningLock(Settings().workspace_root / '.locks'):
        await session.execute(text('BEGIN IMMEDIATE'))
        try:
            row = await _get_host(host_id, session)
            await _assert_snapshot_member_free(session, vmid)
            _assert_vmid_safe(row, vmid, 'configure')
            await _claims(session, vmid)
            async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10) as cli:
                before, config = await _review(cli, row, vmid)
                if not hmac.compare_digest(before.digest, body.digest):
                    raise _error('VM_CONFIG_STALE', 'The guest or registered target changed. Refresh and review again.')
                if key in before.pending:
                    raise _error('VM_HARDWARE_PENDING', 'The selected device has pending Proxmox changes. Wait until it is settled before editing it.')
                if isinstance(body, NicUpdate):
                    nic = nic_view(key, config.get(key))
                    if not nic.editable:
                        raise _error('VM_HARDWARE_UNSUPPORTED', 'Select an existing NIC with an unambiguous readable configuration.')
                    changes = body.changes.model_dump(exclude_unset=True)
                    if 'bridge' in changes and changes['bridge'] != nic.bridge:
                        bridges = await _read_data(cli, row, 'network', {'type': 'any_bridge'})
                        if not isinstance(bridges, list) or not any(isinstance(bridge, dict) and bridge.get('iface') == changes['bridge']
                            and bridge.get('type') in ('bridge', 'OVSBridge', 'vnet') and bridge.get('active') in (True, 1) for bridge in bridges):
                            raise _error('VM_HARDWARE_BRIDGE', 'The requested bridge is not verified active on the selected node.')
                    value = patch_nic(config[key], changes)
                    if value == config[key]:
                        return HardwareResult(status='configured', review=before)
                    return await _dispatch(cli, row, vmid, key, {'digest': config['digest'], key: value}, 'qmconfig', nic_view(key, value))
                disk = disk_view(key, config.get(key))
                if disk is None or not disk.editable:
                    raise _error('VM_HARDWARE_UNSUPPORTED', 'Choose an existing data disk with a readable volume and size. New disks, CD-ROMs and special disks are not supported.')
                requested = body.size_gb * 1024**3
                if requested <= disk.size_bytes:
                    raise _error('VM_DISK_GROWTH_ONLY', 'The requested size must be larger than the current disk. Shrinking is not supported.')
                pool = await _read_data(cli, row, f'storage/{quote(disk.pool, safe="")}/status')
                if not isinstance(pool, dict) or pool.get('active') not in (True, 1) or pool.get('enabled') not in (True, 1) or type(pool.get('avail')) is not int or pool['avail'] < requested - disk.size_bytes or 'images' not in str(pool.get('content', '')).split(','):
                    raise _error('VM_DISK_CAPACITY', 'The disk storage pool does not have verified available space for this growth.')
                expected = disk.model_copy(update={'size_bytes': requested})
                return await _dispatch(cli, row, vmid, key, {'digest': config['digest'], 'disk': key, 'size': f'{body.size_gb}G'}, 'resize', expected)
        finally:
            await session.rollback()


@router.put('/hosts/{host_id}/vms/{vmid}/hardware/nics/{nic_id}', response_model=HardwareResult)
async def update_nic(host_id: str, vmid: VmId, nic_id: NicId, body: NicUpdate, session: AsyncSession = Depends(_session)):
    return await _update(host_id, vmid, nic_id, body, session)


@router.put('/hosts/{host_id}/vms/{vmid}/hardware/disks/{disk_id}/grow', response_model=HardwareResult)
async def grow_disk(host_id: str, vmid: VmId, disk_id: DiskId, body: DiskGrowth, session: AsyncSession = Depends(_session)):
    return await _update(host_id, vmid, disk_id, body, session)
