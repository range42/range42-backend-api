"""Legacy pure VMID scans and SSH ControlMaster namespace helpers.

The in-process lock only serializes scans of the caller's reserved set; it does
not persist or own reservations. Durable authoring leases are implemented in
allocation_reservations and exposed by the typed Proxmox reservation API.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.core.vmid_guard import DEFAULT_PROTECTED_RANGES

_ALLOC_LOCK = asyncio.Lock()


def _is_protected(v: int, host_overrides: list[list[int]] | None) -> bool:
    for lo, hi in DEFAULT_PROTECTED_RANGES:
        if lo <= v <= hi:
            return True
    if host_overrides:
        for lo, hi in host_overrides:
            if lo <= v <= hi:
                return True
    return False


def allocate_vmids(
    *,
    start: int,
    count: int,
    reserved: set[int],
    host_overrides: list[list[int]] | None,
) -> list[int]:
    out: list[int] = []
    v = start
    while len(out) < count and v < 100000:
        if v not in reserved and not _is_protected(v, host_overrides):
            out.append(v)
        v += 1
    if len(out) < count:
        raise RuntimeError(f"Exhausted VMID range starting at {start}")
    return out


async def allocate_vmids_locked(**kwargs) -> list[int]:
    async with _ALLOC_LOCK:
        return allocate_vmids(**kwargs)


def ssh_controlmaster_env(
    *, deployment_id: str, home: Path | None = None
) -> dict[str, str]:
    root = Path(home or os.path.expanduser("~")) / ".ssh" / "range42"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"control-{deployment_id}-%r@%h:%p"
    return {
        "ANSIBLE_SSH_ARGS": (
            "-o ControlMaster=auto "
            f"-o ControlPath={path} "
            "-o ControlPersist=60s "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null"
        )
    }
