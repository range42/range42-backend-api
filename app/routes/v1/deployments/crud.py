"""/v1/deployments CRUD (list/create/get).

Creating a deployment scaffolds the workspace (Workspace.create) which
enforces the local-FS invariant per spec §8. Proxmox token provisioning
is best-effort if the app has a vault password file resolvable; otherwise
the deployment still persists and preflight surfaces AUTH_FAILED later.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.errors import Range42Error, WorkspaceNonLocalFsError
from app.core.models import Deployment
from app.core.workspace import Workspace, WorkspaceError
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import DeploymentCreate, DeploymentOut

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.get("/", response_model=Page[DeploymentOut])
async def list_deployments(session: AsyncSession = Depends(_session),
                           offset: int = 0, limit: int = 100):
    rows = (await session.execute(
        select(Deployment).offset(offset).limit(limit))).scalars().all()
    total = len((await session.execute(select(Deployment))).scalars().all())
    return Page[DeploymentOut](
        items=[DeploymentOut.model_validate(r, from_attributes=True) for r in rows],
        total=total, offset=offset, limit=limit,
    )


@router.post("/", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def create_deployment(payload: DeploymentCreate,
                            session: AsyncSession = Depends(_session)):
    try:
        ws = Workspace.create(
            codename=payload.codename,
            scenario_label=payload.scenario_label,
            workspace_root=settings.workspace_root,
        )
    except WorkspaceError as e:
        raise WorkspaceNonLocalFsError(
            message=e.message,
            details=[{"field": "workspace_root", "reason": e.message}],
        ) from e
    # Best-effort proxmox token provisioning: requires an app-level vault
    # password file and a proxmox_token secret in the payload. Missing
    # pieces fall through silently — preflight catches the failure mode.
    try:
        from app.core.vault import VaultManager
        vp = VaultManager().vault_file
        if (vp
                and Path(vp).exists()
                and payload.secrets
                and "proxmox_token" in payload.secrets):
            from app.core.proxmox_secrets import provision_proxmox_token
            provision_proxmox_token(
                workspace=ws.path,
                host_id=payload.target_host_id,
                api_url="",
                token_id="",
                token_secret=payload.secrets["proxmox_token"],
                vault_password_file=Path(vp),
            )
    except Exception:  # noqa: BLE001 — best-effort; preflight is source of truth
        pass
    row = Deployment(
        id=uuid.uuid4().hex[:16],
        codename=payload.codename,
        scenario_label=payload.scenario_label,
        project_id=payload.project_id,
        target_host_id=payload.target_host_id,
        catalog_sha=payload.catalog_sha,
        project_sha=payload.project_sha,
        team_count=payload.team_count,
        state="pending",
        workspace_path=str(ws.path),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DeploymentOut.model_validate(row, from_attributes=True)


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(deployment_id: str, session: AsyncSession = Depends(_session)):
    row = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    return DeploymentOut.model_validate(row, from_attributes=True)
