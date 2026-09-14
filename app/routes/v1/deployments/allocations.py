"""Read committed assignments; release only after independent VM absence checks."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import APIRouter, Response
from sqlalchemy import select, text

from app.core import config, db
from app.core.allocation_models import DeploymentAllocation
from app.core.allocation_occupancy import vmid_is_free, unavailable
from app.core.allocation_reservations import _error
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES
from app.core.deployment_allocations import validate_binding
from app.core.locks import ProvisioningLock
from app.core.models import Attempt, Deployment, ProxmoxHost, WorkspaceLock
from app.core.proxmox_tls import proxmox_verify
from app.schemas.v1.deployments import DeploymentAllocationOut

router = APIRouter()


async def _claim(session, deployment_id):
    claim = await session.scalar(select(DeploymentAllocation).where(DeploymentAllocation.deployment_id == deployment_id))
    if claim is None:
        raise _error("NOT_FOUND", "This deployment has no committed allocation record.", 404)
    return claim


async def _idle_binding(session, claim):
    deployment = await session.get(Deployment, claim.deployment_id)
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    validate_binding(claim, deployment, host)
    active = await session.scalar(select(Attempt.id).where(
        Attempt.deployment_id == deployment.id, Attempt.state.not_in(TERMINAL_ATTEMPT_STATES)))
    if deployment.state == "unknown" or active is not None or await session.get(WorkspaceLock, deployment.id):
        raise _error("BUSY", "Resolve active or unknown attempts and held workspace locks before releasing this deployment's assignments.")
    return deployment, host


@router.get("/{deployment_id}/allocations", response_model=DeploymentAllocationOut)
async def get_deployment_allocations(deployment_id: str):
    async with db.get_session_factory()() as session:
        claim = await _claim(session, deployment_id)
        return {"deployment_id": claim.deployment_id, "project_sha": claim.project_sha,
                "host_id": claim.host_id, "node_name": claim.node_name,
                "assignments": claim.assignments, "created_at": claim.created_at}


@router.delete("/{deployment_id}/allocations", status_code=204)
async def release_deployment_allocations(deployment_id: str):
    factory = db.get_session_factory()
    # Full runners inherit this lock, so even an API restart cannot mistake
    # their stale observer records for proof that provisioning has stopped.
    with ProvisioningLock(Path(config.settings.workspace_root) / ".locks"):
        async with factory() as session:
            claim = await _claim(session, deployment_id)
            deployment, host = await _idle_binding(session, claim)
            observed = (claim.id, deployment.current_attempt_id)
            vmids = [vm["vm_id"] for vm in claim.assignments]
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(verify=proxmox_verify(), timeout=10, follow_redirects=False) as client:
                semaphore = asyncio.Semaphore(8)
                async def absent(vmid):
                    async with semaphore:
                        return await vmid_is_free(client, host, vmid)
                if not all(await asyncio.gather(*(absent(vmid) for vmid in vmids))):
                    raise _error("IN_USE", "At least one allocated VM ID still exists in Proxmox. Teardown and verify the guests before releasing their assignments.")
        except TimeoutError as exc:
            raise unavailable("VM absence verification timed out; all committed assignments were retained.") from exc
        # Network checks never hold the writer. Re-read the binding/attempt
        # pointer under serialization before allowing authors to reuse IDs.
        async with factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            fresh = await _claim(session, deployment_id)
            deployment, fresh_host = await _idle_binding(session, fresh)
            if observed != (fresh.id, deployment.current_attempt_id) or (host.api_url, host.node_name) != (fresh_host.api_url, fresh_host.node_name):
                raise _error("BUSY", "The deployment changed while verifying absence; its assignments were retained. Retry the release.")
            await session.delete(fresh)
            await session.commit()
    return Response(status_code=204)
