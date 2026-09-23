"""Conservative occupancy from the installed ledger and current manifests.

The union retains stale ledger reservations and includes newly added manifests.
It never makes an undeployed scenario's identifiers available merely because a
checked-in aggregate has not yet been regenerated.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from pathlib import Path

from app.core.allocation_occupancy import Occupancy, unavailable

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 16384


def _read(path: Path, root: Path) -> bytes:
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("symlinked reservation source")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("invalid reservation source size or type")
        data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("reservation source exceeds size limit")
        return data


def _record(result: Occupancy, row: dict) -> None:
    if not isinstance(row, dict) or type(row.get("vm_id")) is not int or not 100 <= row["vm_id"] <= 999999999:
        raise ValueError("invalid reserved VMID")
    result.vmids.add(row["vm_id"])
    nics = row.get("nics", [])
    if not isinstance(nics, list) or len(nics) > 32:
        raise ValueError("invalid reserved interfaces")
    # Include primary legacy fields too, so inconsistent old exports cannot
    # accidentally release an address when a NIC array is added.
    for nic in [row, *nics]:
        if not isinstance(nic, dict):
            raise ValueError("invalid reserved interface")
        bridge, address = nic.get("bridge"), nic.get("ip")
        if bridge is None and address is None:
            continue  # ID-only shared template references carry no address.
        if not isinstance(bridge, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,14}", bridge) or not isinstance(address, str):
            raise ValueError("invalid reserved address")
        ip = ipaddress.ip_interface(address).ip
        if ip.version == 4:
            result.addresses.add((bridge, str(ip)))


def load_installed_reservations(playbooks_root: Path) -> Occupancy:
    """Read bounded immutable runtime files; malformed sources stop allocation."""
    try:
        root = playbooks_root.resolve(strict=True)
        scenarios = root / "scenarios"
        data = _read(scenarios / "_reserved.json", root)
        total = len(data)
        entries = [json.loads(line) for line in data.splitlines() if line.strip()]
        manifests = sorted(scenarios.glob("*/manifest/scenario_vms.json"))
        if len(manifests) > 1024:
            raise ValueError("too many scenario manifests")
        for path in manifests:
            data = _read(path, root)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("installed reservation sources exceed size limit")
            manifest = json.loads(data)
            if not isinstance(manifest, dict):
                raise ValueError("invalid scenario manifest")
            for key in ("vms", "templates"):
                rows = manifest.get(key, [])
                if not isinstance(rows, list):
                    raise ValueError("invalid manifest reservations")
                entries.extend(rows)
            if len(entries) > MAX_ENTRIES:
                raise ValueError("too many scenario reservations")
        if len(entries) > MAX_ENTRIES:
            raise ValueError("too many scenario reservations")
        result = Occupancy()
        for row in entries:
            _record(result, row)
        return result
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise unavailable("Cannot read the installed scenario reservations. Check API_BACKEND_WWWAPP_PLAYBOOKS_DIR, scenarios/_reserved.json and scenario manifests before allocating.") from exc
