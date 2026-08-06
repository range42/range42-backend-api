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
    await session.commit()
    await session.refresh(row)

    # Everything below mutates the workspace, so it runs only once the row is
    # persisted — a failed create must never touch another deployment's files.
    #
    # Seed the workspace vault password: deploy_trigger reads
    # <ws>/secrets/vault_pass.txt to set ANSIBLE_VAULT_PASSWORD_FILE and to
    # unlock the SSH keys for the run, and nothing else writes it. Absent or
    # empty leaves an operator-seeded file alone.
    if payload.secrets and payload.secrets.get("vault_password"):
        vault_pass_file = ws.path / "secrets" / "vault_pass.txt"
        vault_pass_file.parent.mkdir(parents=True, exist_ok=True)
        # chmod before the content so the secret is never briefly world-readable.
        vault_pass_file.touch(mode=0o600, exist_ok=True)
        vault_pass_file.chmod(0o600)
        vault_pass_file.write_text(payload.secrets["vault_password"])

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
