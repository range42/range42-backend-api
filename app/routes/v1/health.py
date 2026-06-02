"""/v1/health + /v1/health/ready — liveness and readiness probes."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.models import ProxmoxHost, Source

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@router.get("/health")
async def liveness():
    return {"status": "ok", "timestamp": _now()}


@router.get("/health/ready")
async def readiness(session: AsyncSession = Depends(_session)):
    checks: dict[str, dict] = {}
    try:
        mode = (await session.execute(text("PRAGMA journal_mode"))).scalar()
        checks["sqlite_wal"] = {"ok": str(mode).lower() == "wal", "mode": mode}
    except Exception as e:  # noqa: BLE001
        checks["sqlite_wal"] = {"ok": False, "err": str(e)}
    try:
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        probe = settings.workspace_root / ".range42-probe"
        probe.write_text("x")
        probe.unlink()
        checks["workspace_writable"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["workspace_writable"] = {"ok": False, "err": str(e)}
    # Proxmox reachability — best-effort across registered hosts.
    hosts = (await session.execute(select(ProxmoxHost))).scalars().all()
    host_results: list[dict] = []
    async with httpx.AsyncClient(verify=False, timeout=3) as cli:
        for h in hosts:
            try:
                r = await cli.get(
                    f"{h.api_url.rstrip('/')}/api2/json/version",
                    headers={"Authorization": f"PVEAPIToken={h.token_ref}"},
                )
                host_results.append({"id": h.id, "ok": r.status_code < 500,
                                     "status": r.status_code})
            except httpx.ConnectError:
                host_results.append({"id": h.id, "ok": False,
                                     "status": "unreachable"})
            except Exception as e:  # noqa: BLE001
                host_results.append({"id": h.id, "ok": False,
                                     "status": "error", "err": str(e)})
    checks["proxmox"] = {
        "ok": all(x["ok"] for x in host_results) if host_results else True,
        "hosts": host_results,
    }
    sources = (await session.execute(select(Source))).scalars().all()
    checks["git"] = {"ok": True, "sources_registered": len(sources)}
    overall = all(c.get("ok") for c in checks.values())
    return {"ready": overall, "checks": checks, "timestamp": _now()}
