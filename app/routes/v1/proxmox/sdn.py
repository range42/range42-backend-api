"""Read SDN configuration without applying, adopting or deleting networks."""
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Path, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Range42Error
from app.core.proxmox_read import list_proxmox_data, ProxmoxReadError
from app.core.proxmox_tls import proxmox_verify
from app.core.scenario_networks import _subnet_cidr
from app.routes.v1.proxmox._helpers import _get_host, _session
from app.schemas.v1.sdn import SdnPage, SdnSubnet, SdnView, SdnVnet, SdnZone

router = APIRouter()
Offset = Annotated[int, Query(ge=0, le=100000)]
Limit = Annotated[int, Query(ge=1, le=500)]


def unavailable():
    return Range42Error(status=502, error='sdn_inventory_unavailable', code='SDN_INVENTORY_UNAVAILABLE',
                        message='SDN inventory could not be read completely. Check the registered token permissions and PVE connectivity; no network change was sent.')


def normalize(row, kind):
    state = row.get('state')
    if state is not None and (not isinstance(state, str) or len(state) > 64):
        raise ValueError()
    common = {'state': state, 'has_pending': bool(row.get('pending')) or state not in (None, 'unchanged')}
    if kind == 'zone':
        nodes = row.get('nodes') or []
        if isinstance(nodes, str):
            nodes = [node for node in nodes.split(',') if node]
        return SdnZone(zone=row['zone'], type=row['type'], nodes=nodes, **common)
    if kind == 'vnet':
        return SdnVnet(vnet=row['vnet'], zone=row['zone'], **common)
    network = _subnet_cidr(row)
    gateway = str(ip_address(row['gateway'])) if row.get('gateway') else None
    if gateway and ip_address(gateway).version != network.version:
        raise ValueError()
    snat = row.get('snat', 0)
    if snat not in (0, 1, '0', '1', False, True):
        raise ValueError()
    return SdnSubnet(subnet=row['subnet'], vnet=row['vnet'], cidr=str(network), gateway=gateway,
                     snat=str(snat).lower() in ('1', 'true'), **common)


async def inventory(session, host_id, path, kind, view, offset, limit):
    host = await _get_host(host_id, session)
    try:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=15) as client:
            rows = await list_proxmox_data(client, host, path, params={view: 1})
        if len(rows) > 4096:
            raise ValueError()
        items = sorted([normalize(row, kind) for row in rows], key=lambda row: getattr(row, kind))
        if len({getattr(row, kind) for row in items}) != len(items):
            raise ValueError()
    except (ProxmoxReadError, ValidationError, ValueError, TypeError, KeyError):
        raise unavailable() from None
    return {'items': items[offset:offset + limit], 'total': len(items), 'offset': offset, 'limit': limit, 'view': view}


@router.get('/hosts/{host_id}/sdn/zones', response_model=SdnPage[SdnZone])
async def zones(host_id: str, view: SdnView = 'running', offset: Offset = 0, limit: Limit = 100,
                session: AsyncSession = Depends(_session)):
    return await inventory(session, host_id, '/cluster/sdn/zones', 'zone', view, offset, limit)


@router.get('/hosts/{host_id}/sdn/vnets', response_model=SdnPage[SdnVnet])
async def vnets(host_id: str, view: SdnView = 'running', offset: Offset = 0, limit: Limit = 100,
                session: AsyncSession = Depends(_session)):
    return await inventory(session, host_id, '/cluster/sdn/vnets', 'vnet', view, offset, limit)


@router.get('/hosts/{host_id}/sdn/vnets/{vnet}/subnets', response_model=SdnPage[SdnSubnet])
async def subnets(host_id: str, vnet: Annotated[str, Path(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')],
                  view: SdnView = 'running', offset: Offset = 0, limit: Limit = 100,
                  session: AsyncSession = Depends(_session)):
    return await inventory(session, host_id, f'/cluster/sdn/vnets/{quote(vnet, safe="")}/subnets', 'subnet', view, offset, limit)
