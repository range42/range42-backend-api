"""Owner-token authoring leases, available before the first project Git save."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, Response

from app.core import config, db
from app.core.allocation_ledger import load_installed_reservations
from app.core.allocation_occupancy import unavailable
from app.core.allocation_reservations import get_or_release, reserve, token_digest
from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.proxmox_tls import proxmox_verify
from app.schemas.v1.allocation import AllocationLease, AllocationRequest

router = APIRouter()


@router.post("/hosts/{host_id}/reservations", response_model=AllocationLease)
async def allocate(host_id: str, payload: AllocationRequest,
                   x_range42_reservation_token: str = Header(min_length=32, max_length=128)):
    token_digest(x_range42_reservation_token)
    factory = db.get_session_factory()
    async with factory() as session:
        host = await session.get(ProxmoxHost, host_id)
        if host is None:
            raise Range42Error(code="NOT_FOUND", status=404, message="The selected Proxmox host does not exist.")
    if not config.settings.wwwapp_playbooks_dir:
        raise unavailable("Configure API_BACKEND_WWWAPP_PLAYBOOKS_DIR so installed scenario reservations can be checked before allocating.")
    reserved = await asyncio.to_thread(load_installed_reservations, Path(config.settings.wwwapp_playbooks_dir))
    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10, follow_redirects=False) as client:
        return await reserve(factory, host, payload, x_range42_reservation_token, client, reserved=reserved)


@router.get("/hosts/{host_id}/reservations/{reservation_id}", response_model=AllocationLease)
async def get_allocation(host_id: str, reservation_id: str,
                         x_range42_reservation_token: str = Header(min_length=32, max_length=128)):
    return await get_or_release(db.get_session_factory(), host_id, reservation_id, x_range42_reservation_token)


@router.delete("/hosts/{host_id}/reservations/{reservation_id}", status_code=204)
async def release_allocation(host_id: str, reservation_id: str,
                             x_range42_reservation_token: str = Header(min_length=32, max_length=128)):
    await get_or_release(db.get_session_factory(), host_id, reservation_id, x_range42_reservation_token, release=True)
    return Response(status_code=204)
