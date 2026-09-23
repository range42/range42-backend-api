"""Deployment ownership is serialized with draft leases, never inferred from expiry.

Only literal VM IDs and configured NIC addresses are claimed. This is not subnet
ownership, IPAM, or authorization to reuse an existing guest on a full retry.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from ipaddress import IPv4Address
from pathlib import Path

from sqlalchemy import select, text

from app.core.allocation import _is_protected
from app.core.allocation_models import AllocationReservation, DeploymentAllocation
from app.core.allocation_reservations import _error, _ownership, _protected, _utc, token_digest
from app.core.models import Deployment, ProxmoxHost
from app.core.scenario_manifest import validate_vm_manifest


def manifest_assignments(directory: Path) -> list[dict]:
    path = directory / "manifest" / "scenario_vms.json"
    try:
        if not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size > 1024 * 1024:
            raise ValueError("manifest bounds")
        document = validate_vm_manifest(json.loads(path.read_text()))
        vms = document["vms"]
        if not isinstance(vms, list) or len(vms) > 4096:
            raise ValueError("VM bounds")
        result = []
        for vm in vms:
            vmid = vm["vm_id"]
            if type(vmid) is not int or not 100 <= vmid <= 999999999:
                raise ValueError("VM ID")
            # Legacy pinned scenarios can declare IDs only. Preserve that
            # contract, without inventing addresses absent from their manifest.
            nics = vm.get("nics")
            if nics is None:
                nics = [{"index": 0, "bridge": vm["bridge"], "ip": vm["ip"]}] if vm.get("ip") and vm.get("bridge") else []
            if not isinstance(nics, list) or len(nics) > 32:
                raise ValueError("NIC bounds")
            normalized = []
            for index, nic in enumerate(nics):
                if not isinstance(nic, dict) or type(nic.get("index")) is not int or nic["index"] != index or not isinstance(nic.get("bridge"), str):
                    raise ValueError("NIC identity")
                normalized.append({"index": index, "bridge": nic["bridge"], "ip": str(IPv4Address(nic["ip"]))})
            result.append({"vm_id": vmid, "vm_name": vm.get("vm_name"), "nics": normalized})
        ids, addresses = occupied(result)
        if len(ids) != len(result) or len(addresses) != sum(len(vm["nics"]) for vm in result):
            raise ValueError("duplicate assignment")
        return sorted(result, key=lambda vm: vm["vm_id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _error("MANIFEST_INVALID", "Cannot establish deployment ownership from the bounded VM manifest.") from exc


def occupied(assignments: list[dict]) -> tuple[set[int], set[tuple[str, str]]]:
    return ({vm["vm_id"] for vm in assignments},
            {(nic["bridge"], nic["ip"]) for vm in assignments for nic in vm["nics"]})


def mapping(assignments: list[dict]) -> list[tuple]:
    return sorted((vm["vm_id"], tuple(sorted((nic["index"], nic["bridge"], nic["ip"]) for nic in vm["nics"])))
                  for vm in assignments)


def validate_binding(claim: DeploymentAllocation, deployment: Deployment, host: ProxmoxHost | None):
    if host is None or (claim.host_id, claim.api_url, claim.node_name, claim.project_sha) != (
        deployment.target_host_id, host.api_url, host.node_name, deployment.project_sha,
    ):
        raise _error("TARGET_CHANGED", "This deployment's saved target or revision changed. Restore its recorded binding before operating on its allocations.")


async def bind(session, deployment: Deployment, host: ProxmoxHost, assignments: list[dict], *,
               reservation_id: str | None = None, token: str | None = None) -> DeploymentAllocation:
    """Caller holds the SQLite writer transaction; commit belongs to the caller."""
    now = datetime.now(timezone.utc)
    existing = await session.scalar(select(DeploymentAllocation).where(DeploymentAllocation.deployment_id == deployment.id))
    if existing:
        validate_binding(existing, deployment, host)
        if existing.assignments != assignments:
            raise _error("MAPPING_CHANGED", "The saved scenario no longer matches this deployment's committed VM/NIC identities.")
        return existing
    lease = None
    if reservation_id:
        if not token:
            raise _error("OWNERSHIP", "Provide the private reservation token to transfer this draft allocation.", 403)
        lease = await session.get(AllocationReservation, reservation_id)
        if lease is None or _utc(lease.expires_at) <= now:
            raise _error("EXPIRED", "The draft reservation expired or was consumed. Reallocate and review the mapping before deployment.")
        _ownership(lease, token_digest(token), host.id)
        if (lease.api_url, lease.node_name) != (host.api_url, host.node_name):
            raise _error("TARGET_CHANGED", "The reservation target changed or predates target binding. Renew and review it before deployment.")
        if mapping(lease.assignments) != mapping(assignments):
            raise _error("MAPPING_CHANGED", "The pinned scenario's VM IDs and NIC assignments differ from the reviewed reservation. Save the current mapping first.")
    ids, addresses = occupied(assignments)
    protected = _protected(host)
    if any(_is_protected(vmid, protected) for vmid in ids):
        raise _error("PROTECTED", "The deployment contains a protected VM ID.")
    claims = list((await session.scalars(select(DeploymentAllocation))).all())
    leases = list((await session.scalars(select(AllocationReservation).where(AllocationReservation.expires_at > now))).all())
    for other in [*claims, *leases]:
        if lease is not None and isinstance(other, AllocationReservation) and other.id == lease.id:
            continue
        other_ids, other_addresses = occupied(other.assignments)
        if ids & other_ids or addresses & other_addresses:
            raise _error("OCCUPIED", "A VM ID or bridge/address pair belongs to another draft or deployment. Review its allocation before deploying.")
    claim = DeploymentAllocation(id=uuid.uuid4().hex, deployment_id=deployment.id,
        project_sha=deployment.project_sha, host_id=host.id, api_url=host.api_url, node_name=host.node_name,
        assignments=assignments, source_assignments=lease.assignments if lease is not None else None, created_at=now)
    session.add(claim)
    if lease is not None:
        await session.delete(lease)
    return claim


async def ensure_for_attempt(factory, deployment: Deployment, directory: Path, *, create: bool,
                             checked_host: ProxmoxHost):
    """The launch gate claims manual/older deployments and verifies existing ones."""
    assignments = manifest_assignments(directory)
    async with factory() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        fresh = await session.get(Deployment, deployment.id)
        if fresh is None or (fresh.target_host_id, fresh.project_sha) != (deployment.target_host_id, deployment.project_sha):
            raise _error("TARGET_CHANGED", "The deployment changed while its scenario was being prepared.")
        host = await session.get(ProxmoxHost, fresh.target_host_id)
        if host is None:
            raise _error("TARGET_CHANGED", "The target registration is unavailable.")
        if (host.api_url, host.node_name, host.protected_vmids_override_json) != (
            checked_host.api_url, checked_host.node_name, checked_host.protected_vmids_override_json,
        ):
            raise _error("TARGET_CHANGED", "The target registration changed after the deployment checks.")
        existing = await session.scalar(select(DeploymentAllocation).where(DeploymentAllocation.deployment_id == fresh.id))
        if existing or (create and assignments):
            await bind(session, fresh, host, assignments)
            await session.commit()
