"""/v1/projects CRUD (list/create/patch)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Project
from app.schemas.v1.common import Page
from app.schemas.v1.projects import ProjectIn, ProjectOut

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.get("/", response_model=Page[ProjectOut])
async def list_projects(
    session: AsyncSession = Depends(_session),
    offset: int = 0,
    limit: int = 100,
):
    rows = (
        await session.execute(select(Project).offset(offset).limit(limit))
    ).scalars().all()
    total = len((await session.execute(select(Project))).scalars().all())
    items = [ProjectOut.model_validate(r, from_attributes=True) for r in rows]
    return Page[ProjectOut](items=items, total=total, offset=offset, limit=limit)


@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectIn, session: AsyncSession = Depends(_session)
):
    row = Project(id=uuid.uuid4().hex[:16], **payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ProjectOut.model_validate(row, from_attributes=True)


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: str,
    payload: ProjectIn,
    session: AsyncSession = Depends(_session),
):
    row = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Project {project_id} not found",
        )
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ProjectOut.model_validate(row, from_attributes=True)
