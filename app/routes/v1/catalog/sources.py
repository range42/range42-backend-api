"""/v1/catalog/sources CRUD."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.catalog_snapshots import catalog_snapshots
from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Source, SourceRepo
from app.schemas.v1.catalog import SourceCredentialsIn, SourceIn, SourceOut, SourceRepoOut
from app.schemas.v1.common import Page

router = APIRouter()


async def _session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session


def _to_out(row: Source) -> SourceOut:
    """Serialise a Source without ever exposing the raw token; has_token only."""
    return SourceOut(
        id=row.id,
        provider=row.provider,
        base_url=row.base_url,
        auth_kind=row.auth_kind,
        has_token=bool(row.token_ref),
        created_at=row.created_at,
        repos=[SourceRepoOut(
            id=repo.id, owner=repo.owner, repo=repo.repo, branch=repo.branch,
            last_refreshed_at=repo.last_refreshed_at,
        ) for repo in row.repos],
    )


@router.get("/sources", response_model=Page[SourceOut])
async def list_sources(
    session: AsyncSession = Depends(_session),
    offset: int = 0,
    limit: int = 100,
):
    total = (await session.execute(select(func.count()).select_from(Source))).scalar_one()
    rows = (
        await session.execute(select(Source).options(selectinload(Source.repos))
                              .order_by(Source.created_at, Source.id).offset(offset).limit(limit))
    ).scalars().all()
    items = [_to_out(r) for r in rows]
    return Page[SourceOut](items=items, total=total, offset=offset, limit=limit)


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceIn, request: Request, session: AsyncSession = Depends(_session)
):
    row = Source(
        id=uuid.uuid4().hex[:16],
        provider=payload.provider,
        base_url=str(payload.base_url),
        auth_kind=payload.auth_kind,
        token_ref=payload.token_ref if payload.auth_kind != "none" else None,
        repos=[SourceRepo(id=uuid.uuid4().hex[:16], **repo.model_dump()) for repo in payload.repos],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row, attribute_names=["created_at", "repos"])
    catalog_snapshots(request).invalidate()
    return _to_out(row)


@router.post("/sources/default", response_model=SourceOut)
async def register_default_source(
    request: Request,
    session: AsyncSession = Depends(_session), kind: Literal["catalog", "bundles"] = "catalog",
):
    """Register the public catalog or the SDN playbooks library without cloning."""
    catalog_snapshots(request).invalidate()
    try:
        return await _register_default_source(session, kind=kind)
    except IntegrityError:
        # Two first-run clients may register the same default simultaneously.
        # The stable source key / unique repository constraint makes one win;
        # the other reads its completed registration after rolling back.
        await session.rollback()
        return await _register_default_source(session, kind=kind)


async def _register_default_source(session: AsyncSession, *, kind: str = "catalog") -> SourceOut:
    repository_name, branch = ("range42-playbooks", "feat-sdn-implementation") if kind == "bundles" else ("range42-catalog", "main")

    sources = (await session.execute(
        select(Source).options(selectinload(Source.repos))
        .order_by(Source.created_at, Source.id)
    )).scalars().all()
    reusable = None
    for source in sources:
        if (source.provider != "github" or source.auth_kind != "none"
                or source.base_url.rstrip("/") != "https://github.com" or source.token_ref):
            continue
        catalog_repo = next((repo for repo in source.repos
                             if repo.owner == "range42" and repo.repo == repository_name), None)
        if len(source.repos) == 1 and catalog_repo is not None and catalog_repo.branch == branch:
            return _to_out(source)
        if not source.repos and reusable is None:
            reusable = source
    if reusable is None:
        existing_ids = {source.id for source in sources}
        default_id = f"range42-public-{kind}"
        suffix = 0
        while default_id in existing_ids:
            suffix += 1
            default_id = f"range42-public-{kind}-{suffix}"
        reusable = Source(id=default_id, provider="github",
                          base_url="https://github.com/", auth_kind="none", repos=[])
        session.add(reusable)
    reusable.repos.append(SourceRepo(id=uuid.uuid4().hex[:16], owner="range42",
                                     repo=repository_name, branch=branch))
    await session.commit()
    await session.refresh(reusable, attribute_names=["created_at", "repos"])
    return _to_out(reusable)


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source_credentials(
    source_id: str, payload: SourceCredentialsIn, request: Request,
    session: AsyncSession = Depends(_session),
):
    row = (await session.execute(
        select(Source).where(Source.id == source_id).options(selectinload(Source.repos))
    )).scalar_one_or_none()
    if row is None:
        raise Range42Error(error="not_found", code="NOT_FOUND", status=404,
                           message=f"Source {source_id} not found")
    row.auth_kind = payload.auth_kind
    row.token_ref = payload.token_ref
    await session.commit()
    catalog_snapshots(request).invalidate()
    return _to_out(row)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: str, request: Request, session: AsyncSession = Depends(_session)
):
    row = (
        await session.execute(select(Source).where(Source.id == source_id))
    ).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Source {source_id} not found",
        )
    await session.delete(row)
    await session.commit()
    catalog_snapshots(request).invalidate()
    return None
