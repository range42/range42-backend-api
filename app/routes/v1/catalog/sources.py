"""/v1/catalog/sources CRUD."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Source
from app.schemas.v1.catalog import SourceIn, SourceOut
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
    )


@router.get("/sources", response_model=Page[SourceOut])
async def list_sources(
    session: AsyncSession = Depends(_session),
    offset: int = 0,
    limit: int = 100,
):
    total = len((await session.execute(select(Source))).scalars().all())
    rows = (
        await session.execute(select(Source).offset(offset).limit(limit))
    ).scalars().all()
    items = [_to_out(r) for r in rows]
    return Page[SourceOut](items=items, total=total, offset=offset, limit=limit)


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceIn, session: AsyncSession = Depends(_session)
):
    row = Source(
        id=uuid.uuid4().hex[:16],
        provider=payload.provider,
        base_url=str(payload.base_url),
        auth_kind=payload.auth_kind,
        token_ref=payload.token_ref,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: str, session: AsyncSession = Depends(_session)
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
    return None
