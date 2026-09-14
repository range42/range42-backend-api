"""Read current CPU, memory and storage capacity for an onboarded host."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.host_capacity import read_host_capacity
from app.routes.v1.proxmox._helpers import _get_host, _session
from app.schemas.v1.capacity import HostCapacity

router = APIRouter()


@router.get("/hosts/{host_id}/capacity", response_model=HostCapacity)
async def host_capacity(host_id: str, session: AsyncSession = Depends(_session)):
    return await read_host_capacity(await _get_host(host_id, session))
