"""/v1/catalog/sources/{id}/refresh — shallow-clone each repo and count manifests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error, SourceUnreachableError
from app.core.logging import get_logger
from app.core.models import Source, SourceRepo
from app.schemas.v1.catalog import SourceRefreshResult

router = APIRouter()
log = get_logger(__name__)


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.post("/sources/{source_id}/refresh", response_model=SourceRefreshResult)
async def refresh_source(
    source_id: str, session: AsyncSession = Depends(_session)
):
    src = (
        await session.execute(select(Source).where(Source.id == source_id))
    ).scalar_one_or_none()
    if src is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Source {source_id} not found",
        )
    started = datetime.now(timezone.utc)
    repos = (
        await session.execute(
            select(SourceRepo).where(SourceRepo.source_id == source_id)
        )
    ).scalars().all()
    entries = 0
    for repo in repos:
        try:
            entries += _count_entries_in_repo(src, repo)
            repo.last_refreshed_at = datetime.now(timezone.utc)
        except Exception as e:
            log.warning(
                "source refresh failure", source_id=source_id, err=str(e)
            )
            raise SourceUnreachableError(
                details=[
                    {
                        "field": "source_id",
                        "reason": f"{src.provider}:{repo.owner}/{repo.repo} unreachable",
                    }
                ]
            )
    await session.commit()
    return SourceRefreshResult(
        source_id=source_id,
        repos_seen=len(repos),
        entries_indexed=entries,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
    )


def _count_manifests(root: Path) -> int:
    """Count catalog entries under ``root`` using the shared walker.

    Delegates to :func:`app.routes.v1.catalog.entries._discover` so the
    refresh count always agrees with what ``/v1/catalog/entries`` surfaces
    (range42.yaml, meta.json, and meta/main.yml — not just range42.yaml).
    """
    from app.routes.v1.catalog.entries import _discover

    return len(_discover(root))


def _count_entries_in_repo(src: Source, repo: SourceRepo) -> int:
    import tempfile

    import git  # type: ignore

    from app.core.project import _redact_authed_url, authed_url

    url = authed_url(
        f"{str(src.base_url).rstrip('/')}/{repo.owner}/{repo.repo}.git",
        src.token_ref,
    )
    with tempfile.TemporaryDirectory() as td:
        try:
            git.Repo.clone_from(url, td, depth=1, branch=repo.branch)
        except Exception as e:
            raise SourceUnreachableError(
                details=[{"field": "source_id",
                          "reason": _redact_authed_url(
                              f"{repo.owner}/{repo.repo}: {e}")}]
            )
        return _count_manifests(Path(td))
