"""Read current node and pool capacity without treating unreadable data as zero."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
from urllib.parse import quote

import httpx

from app.core.models import ProxmoxHost
from app.core.proxmox_read import ProxmoxReadError, list_proxmox_data, read_proxmox_data
from app.core.proxmox_tls import proxmox_verify
from app.schemas.v1.capacity import CapacityIssue, CpuCapacity, HostCapacity, MemoryCapacity, StorageCapacity

LIMITATIONS = [
    "CPU counts describe logical hardware CPUs; utilization is a current sample, not exclusive free cores.",
    "Storage visibility depends on API permissions; absent pools may be hidden from this token.",
    "Measurements are not reservations. Other guests and external Proxmox writers can change capacity immediately.",
    "Thin provisioned storage, snapshots, metadata and shared backing devices affect actual allocation.",
]


def _integer(value, *, positive=False):
    return value if type(value) is int and value >= (1 if positive else 0) else None


def _flag(value):
    return bool(value) if type(value) in (bool, int) and value in (0, 1) else None


def normalize_node_capacity(data) -> tuple[CpuCapacity, MemoryCapacity, list[CapacityIssue]]:
    data = data if isinstance(data, dict) else {}
    info = data.get("cpuinfo") if isinstance(data.get("cpuinfo"), dict) else {}
    memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    utilization = data.get("cpu")
    if type(utilization) not in (int, float) or not math.isfinite(utilization) or not 0 <= utilization <= 1:
        utilization = None
    cpu = CpuCapacity(logical_cpus=_integer(info.get("cpus"), positive=True), utilization=utilization)
    ram = MemoryCapacity(total_bytes=_integer(memory.get("total"), positive=True),
                         used_bytes=_integer(memory.get("used")), free_bytes=_integer(memory.get("free")))
    for field in ("used_bytes", "free_bytes"):
        value = getattr(ram, field)
        if value is not None and ram.total_bytes is not None and value > ram.total_bytes:
            setattr(ram, field, None)
    issues = []
    if cpu.logical_cpus is None or cpu.utilization is None:
        issues.append(CapacityIssue(code="CPU_CAPACITY_UNKNOWN", resource="cpu", message="Proxmox did not provide a valid logical CPU count and utilization sample."))
    if None in (ram.total_bytes, ram.used_bytes, ram.free_bytes):
        issues.append(CapacityIssue(code="MEMORY_CAPACITY_UNKNOWN", resource="memory", message="Proxmox did not provide complete, valid node memory measurements."))
    return cpu, ram, issues


def _normalize_storage(rows: list[dict]) -> tuple[list[StorageCapacity], list[CapacityIssue]]:
    pools, issues, seen = [], [], set()
    if not rows:
        issues.append(CapacityIssue(code="STORAGE_VISIBILITY_UNKNOWN", resource="storage", message="No storage pools were visible. The token may lack Datastore.Audit or Datastore.AllocateSpace permissions."))
    for row in rows:
        name = row.get("storage")
        if not isinstance(name, str) or not name or name in seen:
            issues.append(CapacityIssue(code="STORAGE_DATA_INVALID", resource="storage", message="Proxmox returned an invalid or duplicate pool identifier."))
            continue
        seen.add(name)
        content = row.get("content")
        pool = StorageCapacity(storage=name, type=row.get("type") if isinstance(row.get("type"), str) else None,
                               content=content.split(",") if isinstance(content, str) else [],
                               enabled=_flag(row.get("enabled")), active=_flag(row.get("active")), shared=_flag(row.get("shared")),
                               total_bytes=_integer(row.get("total"), positive=True), used_bytes=_integer(row.get("used")),
                               free_bytes=_integer(row.get("avail")))
        if pool.active is False or pool.enabled is False:
            pool.free_bytes = None
            issues.append(CapacityIssue(code="STORAGE_INACTIVE", resource=f"storage:{name}", message=f"Storage {name} is inactive or disabled; its reported free space cannot be used."))
        elif (pool.active is None or pool.enabled is None
              or None in (pool.total_bytes, pool.used_bytes, pool.free_bytes)):
            issues.append(CapacityIssue(code="STORAGE_CAPACITY_UNKNOWN", resource=f"storage:{name}", message=f"Proxmox did not provide complete, valid capacity and availability for storage {name}."))
        for field in ("used_bytes", "free_bytes"):
            value = getattr(pool, field)
            if value is not None and pool.total_bytes is not None and value > pool.total_bytes:
                setattr(pool, field, None)
                issues.append(CapacityIssue(code="STORAGE_DATA_INVALID", resource=f"storage:{name}", message=f"Storage {name} reports a capacity value larger than its total."))
        pools.append(pool)
    return pools, issues


async def read_host_capacity(host: ProxmoxHost, *, client: httpx.AsyncClient | None = None) -> HostCapacity:
    if client is None:
        async with httpx.AsyncClient(verify=proxmox_verify(), timeout=8) as owned:
            return await read_host_capacity(host, client=owned)
    node = quote(host.node_name, safe="")
    status, storage = await asyncio.gather(
        read_proxmox_data(client, host, f"/nodes/{node}/status"),
        list_proxmox_data(client, host, f"/nodes/{node}/storage"), return_exceptions=True,
    )
    issues = []
    cpu, memory, pools = CpuCapacity(), MemoryCapacity(), []
    for result in (status, storage):
        if isinstance(result, BaseException) and not isinstance(result, ProxmoxReadError):
            raise result
    if isinstance(status, ProxmoxReadError):
        issues.append(CapacityIssue(code="NODE_CAPACITY_UNAVAILABLE", resource="node", message=str(status)))
    else:
        cpu, memory, node_issues = normalize_node_capacity(status)
        issues.extend(node_issues)
    if isinstance(storage, ProxmoxReadError):
        issues.append(CapacityIssue(code="STORAGE_CAPACITY_UNAVAILABLE", resource="storage", message=str(storage)))
    else:
        pools, pool_issues = _normalize_storage(storage)
        issues.extend(pool_issues)
    measured = cpu.logical_cpus is not None or memory.free_bytes is not None or bool(pools)
    return HostCapacity(host_id=host.id, node_name=host.node_name, observed_at=datetime.now(timezone.utc),
                         status="unavailable" if not measured else "partial" if issues else "available",
                         cpu=cpu, memory=memory, storage=pools, issues=issues, limitations=LIMITATIONS)
