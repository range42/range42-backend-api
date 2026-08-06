"""/v1/deployments CRUD (list/create/get).

Creating a deployment scaffolds the workspace (Workspace.create) which
enforces the local-FS invariant per spec §8. Proxmox token provisioning
is best-effort if the app has a vault password file resolvable; otherwise
the deployment still persists and preflight surfaces AUTH_FAILED later.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.errors import Range42Error, WorkspaceNonLocalFsError
from app.core.logging import get_logger
from app.core.models import Deployment, Project, ProxmoxHost
from app.core.workspace import Workspace, WorkspaceError
from app.core.workspace_secrets import (
    VaultSeedError,
    provision_host_token,
    vault_seed,
)
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import DeploymentCreate, DeploymentOut

router = APIRouter()
log = get_logger(__name__)


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
    # (codename, scenario_label) is unique and also names the workspace
    # directory, so a duplicate would reuse an existing deployment's
    # workspace. Reject it here rather than letting the commit fail: by then
    # the shared directory has already been mutated, and the IntegrityError
    # surfaces as an opaque 500.
    clash = (await session.execute(
        select(Deployment).where(
            Deployment.codename == payload.codename,
            Deployment.scenario_label == payload.scenario_label,
        )
    )).scalar_one_or_none()
    if clash is not None:
        raise Range42Error(
            error="conflict",
            code="DEPLOYMENT_EXISTS",
            status=409,
            message=(
                f"A deployment named {payload.codename}/"
                f"{payload.scenario_label} already exists"
            ),
            details=[{"field": "codename", "reason": f"in use by {clash.id}"}],
        )

    # Validate the foreign keys here too. They are enforced at DB level
    # (PRAGMA foreign_keys=ON), so a bad reference would otherwise surface
    # from the flush below as an IntegrityError indistinguishable from the
    # uniqueness clash — and get reported as "already exists".
    for field, model, value in (
        ("project_id", Project, payload.project_id),
        ("target_host_id", ProxmoxHost, payload.target_host_id),
    ):
        found = (await session.execute(
            select(model.id).where(model.id == value)
        )).scalar_one_or_none()
        if found is None:
            raise Range42Error(
                error="not_found",
                code="NOT_FOUND",
                status=404,
                message=f"{model.__name__} {value} not found",
                details=[{"field": field, "reason": "no such row"}],
            )

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
    # Flush, do not commit: this reserves (codename, scenario_label) at the DB
    # level — closing the race the pre-check above cannot — while leaving the
    # transaction open. The secret is written inside that window, so a failed
    # write rolls the row back and the caller can simply retry. Committing
    # first would strand a deployment whose workspace has no password, and the
    # retry would then hit the 409 rather than fixing itself.
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        # Only the workspace-uniqueness constraint means "already exists".
        # Anything else (a reference deleted between the checks above and this
        # flush, say) must not be dressed up as a name clash.
        detail = str(getattr(e, "orig", e))
        if "unique" in detail.lower() or "uq_deployment_workspace" in detail:
            raise Range42Error(
                error="conflict",
                code="DEPLOYMENT_EXISTS",
                status=409,
                message=(
                    f"A deployment named {payload.codename}/"
                    f"{payload.scenario_label} already exists"
                ),
                details=[{"field": "codename", "reason": "created concurrently"}],
            ) from e
        raise Range42Error(
            error="invalid_reference",
            code="INVALID_REFERENCE",
            status=409,
            message="Deployment could not be persisted",
            details=[{"field": "payload", "reason": detail}],
        ) from e

    # The password is durable iff the row is — vault_seed reverts the file
    # if the commit does not happen. See app/core/workspace_secrets.
    try:
        with vault_seed(ws.path, payload.secrets):
            await session.commit()
    except VaultSeedError as e:
        await session.rollback()
        raise Range42Error(
            error="workspace_error",
            code="VAULT_SEED_FAILED",
            status=500,
            message="Could not write the workspace vault password",
            details=[{"field": "secrets.vault_password", "reason": e.reason}],
        ) from e
    except Range42Error:
        raise
    except Exception as e:
        await session.rollback()
        raise Range42Error(
            error="workspace_error",
            code="DEPLOYMENT_PERSIST_FAILED",
            status=500,
            message="Deployment could not be persisted",
            details=[{"field": "deployment", "reason": str(e)}],
        ) from e

    await session.refresh(row)
    provision_host_token(ws.path, payload.target_host_id, payload.secrets)

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
