"""/v1/projects CRUD (list/create/patch)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Response, status
from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment, Project, Source
from app.schemas.v1.common import Page
from app.schemas.v1.projects import ProjectIn, ProjectOut, ProjectPatch

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


async def _project(session: AsyncSession, project_id: str) -> Project:
    row = await session.get(Project, project_id)
    if row is None:
        raise Range42Error(error="not_found", code="NOT_FOUND", status=404,
                           message=f"Project {project_id} not found")
    return row


async def _validate_source(session: AsyncSession, payload: ProjectIn) -> None:
    source = await session.get(Source, payload.source_id)
    if source is None:
        raise Range42Error(error="not_found", code="SOURCE_NOT_FOUND", status=404,
                           message="Selected Git Source not found")
    if payload.repo_owner and "/" in payload.repo_owner and source.provider != "gitlab":
        raise Range42Error(error="validation_error", code="VALIDATION", status=422,
                           message="Nested repository namespaces require a GitLab Source")


async def _update_project(session: AsyncSession, row: Project, payload: ProjectIn) -> ProjectOut:
    await _validate_source(session, payload)
    values = payload.model_dump()
    # A historical deployment must keep resolving the same pinned repository.
    # Apply this condition in the UPDATE, rather than checking a stale ORM row.
    same_binding = and_(*(getattr(Project, name) == values[name] for name in (
        "source_id", "repo_owner", "repo_name", "subdir", "branch_strategy")))
    referenced = exists().where(Deployment.project_id == Project.id)
    changed = await session.execute(update(Project).where(
        Project.id == row.id, or_(~referenced, same_binding),
    ).values(**values).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        await session.rollback()
        raise Range42Error(error="conflict", code="PROJECT_BINDING_IN_USE", status=409,
                           message="This project has deployments; register a new project for a different repository binding")
    await session.commit()
    await session.refresh(row)
    return ProjectOut.model_validate(row, from_attributes=True)


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
    await _validate_source(session, payload)
    row = Project(id=uuid.uuid4().hex[:16], **payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ProjectOut.model_validate(row, from_attributes=True)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, session: AsyncSession = Depends(_session)):
    return ProjectOut.model_validate(await _project(session, project_id), from_attributes=True)


@router.put("/{project_id}", response_model=ProjectOut)
async def register_project(
    payload: ProjectIn,
    response: Response,
    project_id: str = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"),
    session: AsyncSession = Depends(_session),
):
    """Register a browser identity with a backend-owned Git Source binding."""
    row = await session.get(Project, project_id)
    if row is not None:
        return await _update_project(session, row, payload)
    await _validate_source(session, payload)
    row = Project(id=project_id, **payload.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent registration of this identity may have won the insert.
        await session.rollback()
        existing = await session.get(Project, project_id)
        if existing is None:
            raise Range42Error(error="conflict", code="PROJECT_REGISTRATION_CONFLICT", status=409,
                               message="Project registration changed concurrently; retry") from None
        return await _update_project(session, existing, payload)
    response.status_code = status.HTTP_201_CREATED
    await session.refresh(row)
    return ProjectOut.model_validate(row, from_attributes=True)


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: str,
    payload: ProjectPatch,
    session: AsyncSession = Depends(_session),
):
    row = await _project(session, project_id)
    values = {name: getattr(row, name) for name in ProjectIn.model_fields}
    values.update(payload.model_dump(exclude_unset=True))
    try:
        merged = ProjectIn.model_validate(values)
    except ValidationError:
        raise Range42Error(error="validation_error", code="VALIDATION", status=422,
                           message="Project fields do not form a valid repository binding") from None
    return await _update_project(session, row, merged)


@router.post("/{project_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def project_heartbeat(
    project_id: str,
    session: AsyncSession = Depends(_session),
):
    """Liveness ping for an open editing session (#106).

    The edit lock itself is **not** held here: it lives in the git-backed
    project repo, written by the client (``ProjectRepoAdapter.writeLock``).
    This endpoint answers one question for the SharedWorker that polls it —
    "is the backend reachable and does it still know this project?" — so the
    UI can distinguish a live session from a stale one.

    Deliberately does no writes: a heartbeat must stay cheap enough to run
    on a short interval per open editor, and it carries no state the lock
    file does not already hold.

    :returns: 204 when the project exists; 404 otherwise, which the worker
        treats as a failed tick and eventually surfaces as a stale session.
    """
    exists = (
        await session.execute(select(Project.id).where(Project.id == project_id))
    ).scalar_one_or_none()
    if exists is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Project {project_id} not found",
        )
    return None
