"""/v1/proxmox/hosts CRUD + health probe."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthFailedError, Range42Error
from app.core.logging import get_logger
from app.core.models import ProxmoxHost
from app.routes.v1.proxmox._helpers import _session
from app.schemas.v1.common import Page
from app.schemas.v1.proxmox import HostHealth, HostIn, HostOut

router = APIRouter()
log = get_logger(__name__)


def _row_to_out(row: ProxmoxHost) -> HostOut:
    overrides = (
        json.loads(row.protected_vmids_override_json)
        if row.protected_vmids_override_json
        else None
    )
    health = (
        json.loads(row.last_health_check_json)
        if row.last_health_check_json
        else None
    )
    return HostOut(
        id=row.id,
        name=row.name,
        api_url=row.api_url,
        node_name=row.node_name,
        has_token=bool(row.token_ref),
        token_scope=row.token_scope,
        default_bridge=row.default_bridge,
        protected_vmids_override=overrides,
        added_at=row.added_at,
        last_health_check=health,
    )


async def _find_host_by_name(
    session: AsyncSession, name: str
) -> ProxmoxHost | None:
    return (
        await session.execute(
            select(ProxmoxHost).where(ProxmoxHost.name == name)
        )
    ).scalar_one_or_none()


def _refresh_host(row: ProxmoxHost, payload: HostIn, overrides_json: str | None) -> None:
    """Carry a re-registration onto an existing row, id and added_at intact."""
    row.api_url = str(payload.api_url)
    row.node_name = payload.node_name
    row.token_ref = payload.token_ref
    row.token_scope = payload.token_scope
    row.default_bridge = payload.default_bridge
    row.protected_vmids_override_json = overrides_json


@router.get("/hosts", response_model=Page[HostOut])
async def list_hosts(
    session: AsyncSession = Depends(_session),
    offset: int = 0,
    limit: int = 100,
):
    rows = (
        await session.execute(
            select(ProxmoxHost).offset(offset).limit(limit)
        )
    ).scalars().all()
    total = len((await session.execute(select(ProxmoxHost))).scalars().all())
    return Page[HostOut](
        items=[_row_to_out(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/hosts",
    response_model=HostOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": HostOut,
            "description": "Host already registered under this name; updated in place.",
        }
    },
)
async def create_host(
    payload: HostIn,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    """Register a Proxmox host, or refresh the one already under that name.

    The deploy bundle POSTs this on every scenario run, so it has to be
    idempotent. Re-registering keeps the existing row's id — ``deployments``
    reference it by FK — and returns 200 instead of 201. Credentials are part
    of what gets refreshed: a rotated PVE token reaches the backend on the next
    deploy rather than leaving it authenticating with a stale one.
    """
    overrides_json = (
        json.dumps(payload.protected_vmids_override)
        if payload.protected_vmids_override
        else None
    )

    async def _update_in_place(row: ProxmoxHost) -> HostOut:
        # added_at deliberately untouched: it records when this host was first
        # registered, and the dedupe migration keys on it.
        _refresh_host(row, payload, overrides_json)
        await session.commit()
        await session.refresh(row)
        log.info("proxmox_host_reregistered", host_id=row.id, name=row.name)
        response.status_code = status.HTTP_200_OK
        return _row_to_out(row)

    existing = await _find_host_by_name(session, payload.name)
    if existing is not None:
        return await _update_in_place(existing)

    row = ProxmoxHost(
        id=uuid.uuid4().hex[:16],
        name=payload.name,
        api_url=str(payload.api_url),
        node_name=payload.node_name,
        token_ref=payload.token_ref,
        token_scope=payload.token_scope,
        default_bridge=payload.default_bridge,
        protected_vmids_override_json=overrides_json,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Lost the race: another request registered this name between the
        # lookup above and this commit. Recover into the update path instead
        # of surfacing a 500 — the caller asked for a registration and one
        # now exists, which is the outcome they wanted.
        await session.rollback()
        winner = await _find_host_by_name(session, payload.name)
        if winner is None:
            raise
        log.info("proxmox_host_register_race", name=payload.name, host_id=winner.id)
        return await _update_in_place(winner)
    await session.refresh(row)
    return _row_to_out(row)


@router.delete("/hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host(host_id: str, session: AsyncSession = Depends(_session)):
    row = (
        await session.execute(
            select(ProxmoxHost).where(ProxmoxHost.id == host_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Host {host_id} not found",
        )
    await session.delete(row)
    await session.commit()
    return None


@router.get("/hosts/{host_id}/health", response_model=HostHealth)
async def host_health(host_id: str, session: AsyncSession = Depends(_session)):
    row = (
        await session.execute(
            select(ProxmoxHost).where(ProxmoxHost.id == host_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Host {host_id} not found",
        )
    headers = {"Authorization": f"PVEAPIToken={row.token_ref}"}
    start = time.perf_counter()
    status_value = "unreachable"
    sdn_available: bool | None = None
    async with httpx.AsyncClient(verify=False, timeout=5) as cli:
        try:
            version_r = await cli.get(
                f"{row.api_url}/api2/json/version", headers=headers
            )
            if version_r.status_code in (401, 403):
                raise AuthFailedError(
                    details=[
                        {
                            "field": "token_ref",
                            "reason": (
                                "Proxmox API rejected credentials "
                                f"({version_r.status_code})"
                            ),
                        }
                    ]
                )
            status_value = "ok" if version_r.status_code == 200 else "degraded"
            sdn_r = await cli.get(
                f"{row.api_url}/api2/json/cluster/sdn", headers=headers
            )
            sdn_available = sdn_r.status_code == 200
        except httpx.ConnectError as e:
            log.warning("host health connect error", host=row.id, err=str(e))
            status_value = "unreachable"
            sdn_available = None
        except httpx.RequestError as e:
            log.warning("host health request error", host=row.id, err=str(e))
            status_value = "unreachable"
            sdn_available = None
    rtt = int((time.perf_counter() - start) * 1000)
    health = HostHealth(
        status=status_value,
        rtt_ms=rtt,
        sdn_available=sdn_available,
        at=datetime.now(timezone.utc),
    )
    row.last_health_check_json = health.model_dump_json()
    await session.commit()
    return health
