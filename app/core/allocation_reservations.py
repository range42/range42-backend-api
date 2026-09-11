"""SQLite-serialized, expiring authoring leases with read-only PVE checks.

Network requests run outside the database writer lock. A writer re-plans from
current leases before commit; changed candidates are checked and retried. This
coordinates Range42 authors across workers, not independent Proxmox writers.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.allocation import _is_protected
from app.core.allocation_models import AllocationReservation
from app.core.allocation_occupancy import Occupancy, read_occupancy, vmid_is_free
from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.schemas.v1.allocation import AllocationLease, AllocationRequest, AllocationVm

LIMITATIONS = [
    "Reservations exclude the installed scenario ledger and current manifests, including undeployed guests. Other unlinked repositories are outside this reservation scope.",
    "Reservations coordinate this Range42 installation only; external Proxmox writers are not locked. Deployment must recheck availability.",
    "Address checks cover visible cloud-init, LXC and host network configuration; guest-static, DHCP and external-device addresses require explicit reserved_ips or external IPAM.",
    "VMIDs and bridge/address pairs are reserved conservatively across all registered hosts, including aliases and separate clusters.",
]
MAX_PROBES = 4096
MAX_ACTIVE_LEASES = 1000


def _error(code: str, message: str, status: int = 409) -> Range42Error:
    return Range42Error(code=f"ALLOCATION_{code}", status=status, message=message)


def token_digest(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise _error("INVALID", "Provide a 32–128 character URL-safe reservation token in X-Range42-Reservation-Token.", 422)
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _ownership(row: AllocationReservation, token_hash: str, host_id: str):
    if not hmac.compare_digest(row.token_hash, token_hash) or row.host_id != host_id:
        raise _error("OWNERSHIP", "This reservation belongs to another project owner or target host.", 403)


def _lease(row: AllocationReservation) -> AllocationLease:
    return AllocationLease(
        reservation_id=row.id, project_key=row.project_key, host_id=row.host_id,
        node_name=row.node_name, expires_at=_utc(row.expires_at), checked_at=_utc(row.checked_at),
        assignments=row.assignments, limitations=list(LIMITATIONS),
    )


def _protected(host: ProxmoxHost) -> list[list[int]]:
    try:
        overrides = json.loads(host.protected_vmids_override_json or "[]")
        if not isinstance(overrides, list) or len(overrides) > 256 or any(
            not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int for v in pair)
            or not 100 <= pair[0] <= pair[1] <= 999999999 for pair in overrides
        ):
            raise ValueError("invalid overrides")
        return overrides
    except (ValueError, TypeError) as exc:
        raise _error("INVALID", "The target host's protected VMID ranges are invalid; correct its registration first.") from exc


def _retained_nics(vm: AllocationVm, old: dict, networks: dict) -> dict[int, dict]:
    """Match authored NIC identity, with explicit disambiguation of legacy rows."""
    previous = old.get("nics", [])
    if not previous:
        return {}
    keys = [nic.get("nic_key") for nic in previous if nic.get("nic_key") is not None]
    if keys and (len(keys) != len(previous) or len(set(keys)) != len(keys)):
        raise _error("NIC_IDENTITY_REQUIRED", "Stored NIC identities are inconsistent; review the reservation before renewing it.")
    if vm.nics[0].nic_key is None:
        if keys:
            raise _error("NIC_IDENTITY_REQUIRED", "This reservation uses stable NIC keys. Keep those keys when renewing it.")
        return {nic["index"]: nic for nic in previous}
    if keys:
        by_key = {nic["nic_key"]: nic for nic in previous}
        return {nic.index: by_key.get(nic.nic_key, {}) for nic in vm.nics}

    # Old rows did not record edge keys. Network identity is enough only when
    # it names one unmatched old NIC and one new NIC. Explicit addresses settle
    # parallel-link ambiguity without silently swapping their assignments.
    explicit = {(nic.network_id, nic.ip) for nic in vm.nics if nic.ip is not None}
    retained = {}
    for nic in vm.nics:
        if nic.ip is not None:
            continue
        network = networks[nic.network_id]
        candidates = [item for item in previous if (item["network_id"], item["ip"]) not in explicit
                      and all(item.get(key) == getattr(network, key) for key in ("network_id", "bridge", "subnet"))]
        if candidates:
            unresolved = sum(item.network_id == nic.network_id and item.ip is None for item in vm.nics)
            if len(candidates) != 1 or unresolved != 1:
                raise _error("NIC_IDENTITY_REQUIRED", "Keep existing NIC addresses explicit when assigning stable keys to parallel legacy interfaces.")
            retained[nic.index] = candidates[0]
    return retained


def _plan(payload: AllocationRequest, host: ProxmoxHost, rows: list[AllocationReservation], current: AllocationReservation | None,
          occupancy: Occupancy) -> list[dict]:
    overrides = _protected(host)
    networks = {network.network_id: network for network in payload.networks}
    previous = {vm["node_id"]: vm for vm in current.assignments} if current else {}
    unavailable_ids = set(occupancy.vmids)
    unavailable_ips = set(occupancy.addresses)
    for row in rows:
        if current and row.id == current.id:
            continue
        for vm in row.assignments:
            unavailable_ids.add(vm["vm_id"])
            unavailable_ips.update((nic["bridge"], nic["ip"]) for nic in vm["nics"])
    for network in payload.networks:
        unavailable_ips.update((network.bridge, ip) for ip in network.reserved_ips)
        if network.gateway:
            unavailable_ips.add((network.bridge, network.gateway))

    # Reserve manual and retained values before automatic allocation, so request
    # ordering cannot let an automatic choice consume a later manual assignment.
    fixed_ids: dict[str, int] = {}
    fixed_ips: dict[tuple[str, int], str] = {}
    claimed_ids: set[int] = set()
    claimed_ips: set[tuple[str, str]] = set()
    for vm in payload.vms:
        old = previous.get(vm.node_id, {})
        fixed_id = vm.vm_id if vm.vm_id is not None else old.get("vm_id")
        if fixed_id is not None:
            if _is_protected(fixed_id, overrides):
                raise _error("PROTECTED", f"VMID {fixed_id} is in a protected range.")
            if fixed_id in unavailable_ids or fixed_id in claimed_ids:
                raise _error("OCCUPIED", f"VMID {fixed_id} is occupied or already reserved. Choose another ID.")
            fixed_ids[vm.node_id] = fixed_id
            claimed_ids.add(fixed_id)
        old_nics = _retained_nics(vm, old, networks)
        for nic in vm.nics:
            network = networks[nic.network_id]
            old_nic = old_nics.get(nic.index, {})
            unchanged_network = all(old_nic.get(key) == getattr(network, key) for key in ("network_id", "bridge", "subnet"))
            fixed_ip = nic.ip if nic.ip is not None else old_nic.get("ip") if unchanged_network else None
            if fixed_ip is not None:
                subnet = ipaddress.IPv4Network(network.subnet)
                address = ipaddress.IPv4Address(fixed_ip)
                if address not in subnet or address in (subnet.network_address, subnet.broadcast_address):
                    raise _error("INVALID", f"Address {fixed_ip} is not usable in subnet {network.subnet}.")
                pair = (network.bridge, fixed_ip)
                if pair in unavailable_ips or pair in claimed_ips:
                    raise _error("OCCUPIED", f"Address {fixed_ip} on {network.bridge} is configured, reserved, or a gateway.")
                fixed_ips[(vm.node_id, nic.index)] = fixed_ip
                claimed_ips.add(pair)

    assignments = []
    for vm in payload.vms:
        vmid = fixed_ids.get(vm.node_id)
        if vmid is None:
            for candidate in range(payload.vmid_start, min(payload.vmid_end + 1, payload.vmid_start + 100000)):
                if candidate not in unavailable_ids and candidate not in claimed_ids and not _is_protected(candidate, overrides):
                    vmid = candidate
                    break
            if vmid is None:
                raise _error("POOL_EXHAUSTED", "No allocatable VMID remains in the requested range (scan limit 100000 IDs).")
            claimed_ids.add(vmid)
        nics = []
        for nic in sorted(vm.nics, key=lambda item: item.index):
            network = networks[nic.network_id]
            subnet = ipaddress.IPv4Network(network.subnet)
            address = fixed_ips.get((vm.node_id, nic.index))
            if address is None:
                for index, candidate in enumerate(subnet.hosts()):
                    if index >= 4096:
                        break
                    if (network.bridge, str(candidate)) not in unavailable_ips and (network.bridge, str(candidate)) not in claimed_ips:
                        address = str(candidate)
                        break
                if address is None:
                    raise _error("POOL_EXHAUSTED", f"No address remains in {network.subnet} on {network.bridge} (scan limit 4096 addresses).")
                claimed_ips.add((network.bridge, address))
            nics.append({"index": nic.index, **({"nic_key": nic.nic_key} if nic.nic_key is not None else {}),
                         "network_id": network.network_id, "bridge": network.bridge,
                         "subnet": network.subnet, "ip": address, "prefix": subnet.prefixlen, "gateway": network.gateway})
        assignments.append({"node_id": vm.node_id, "vm_id": vmid, "nics": nics})
    return assignments


async def reserve(factory: async_sessionmaker[AsyncSession], host: ProxmoxHost, payload: AllocationRequest,
                  token: str, client: httpx.AsyncClient, *, reserved: Occupancy | None = None) -> AllocationLease:
    token_hash = token_digest(token)
    # Reject a wrong owner before any expensive inventory audit.
    async with factory() as session:
        current = await session.scalar(select(AllocationReservation).where(AllocationReservation.project_key == payload.project_key))
        if current and _utc(current.expires_at) > datetime.now(timezone.utc):
            _ownership(current, token_hash, host.id)
    try:
        async with asyncio.timeout(30):
            occupancy = await read_occupancy(client, host)
            if reserved is not None:
                occupancy.vmids.update(reserved.vmids)
                occupancy.addresses.update(reserved.addresses)
            checked_at = datetime.now(timezone.utc)
            checked_free: set[int] = set()
            probes = 0
            while probes <= MAX_PROBES:
                async with factory() as session:
                    # SQLite writer serialization works across processes and is
                    # held only for ledger reads/planning/write, never HTTP I/O.
                    await session.execute(text("BEGIN IMMEDIATE"))
                    now = datetime.now(timezone.utc)
                    await session.execute(delete(AllocationReservation).where(AllocationReservation.expires_at <= now))
                    rows = list((await session.scalars(select(AllocationReservation))).all())
                    current = next((row for row in rows if row.project_key == payload.project_key), None)
                    if current:
                        _ownership(current, token_hash, host.id)
                        if current.node_name != host.node_name:
                            raise _error("INVALID", "The target node changed; release the old reservation before allocating on another node.")
                    elif len(rows) >= MAX_ACTIVE_LEASES:
                        raise _error("POOL_EXHAUSTED", "The installation has reached 1000 active authoring leases; release an unused lease or wait for expiry.")
                    fresh_host = await session.get(ProxmoxHost, host.id)
                    if fresh_host is None or (fresh_host.api_url, fresh_host.node_name, fresh_host.protected_vmids_override_json) != (host.api_url, host.node_name, host.protected_vmids_override_json):
                        raise _error("INVALID", "The target host registration changed during allocation; retry against its current configuration.")
                    assignments = _plan(payload, host, rows, current, occupancy)
                    pending = sorted({vm["vm_id"] for vm in assignments} - checked_free)
                    if not pending:
                        row = current or AllocationReservation(id=uuid.uuid4().hex, project_key=payload.project_key,
                            host_id=host.id, node_name=host.node_name, token_hash=token_hash)
                        row.assignments = assignments
                        row.checked_at = checked_at
                        row.expires_at = now + timedelta(seconds=payload.lease_seconds)
                        session.add(row)
                        await session.commit()
                        return _lease(row)
                    await session.rollback()
                if probes + len(pending) > MAX_PROBES:
                    break
                semaphore = asyncio.Semaphore(8)
                async def check(vmid: int):
                    async with semaphore:
                        return vmid, await vmid_is_free(client, host, vmid)
                for vmid, free in await asyncio.gather(*(check(vmid) for vmid in pending)):
                    (checked_free if free else occupancy.vmids).add(vmid)
                probes += len(pending)
            raise _error("POOL_EXHAUSTED", "The bounded cluster-wide VMID availability scan is exhausted; choose a narrower free range.")
    except TimeoutError as exc:
        raise _error("OCCUPANCY_UNAVAILABLE", "The bounded Proxmox occupancy audit timed out; no new reservation was saved. Retry when the cluster is responsive.") from exc
    except OperationalError as exc:
        raise _error("BUSY", "The reservation ledger is busy; retry with the same project and reservation token.") from exc


async def get_or_release(factory: async_sessionmaker[AsyncSession], host_id: str, reservation_id: str,
                         token: str, *, release: bool = False) -> AllocationLease | None:
    token_hash = token_digest(token)
    async with factory() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        row = await session.get(AllocationReservation, reservation_id)
        if row is None:
            raise _error("EXPIRED", "This reservation has expired or been released. Request a fresh allocation.")
        _ownership(row, token_hash, host_id)
        if release:
            await session.delete(row)
            await session.commit()
            return None
        if _utc(row.expires_at) <= datetime.now(timezone.utc):
            raise _error("EXPIRED", "This reservation has expired. Reallocate and review the current mapping before applying it.")
        return _lease(row)
