"""/v1/catalog/entries — cross-source browse + single-entry detail.

Each request shallow-clones every registered SourceRepo into a tmpdir,
walks the trees for catalog manifests, and synthesises catalog entry
summaries. Native manifests, bundle playbooks and descriptors, Galaxy roles,
role task entrypoints, Docker assets and gamification manifests are recognised.
Core metadata formats include:

* ``range42.yaml`` — native Range42 manifest (kind/name/description/tags
  read straight from the document).
* ``meta.json`` — container/CTF challenges with an ``x_range42`` namespace.
* ``meta/main.yml`` — Ansible Galaxy roles with a ``galaxy_info`` block.

Detail endpoint also reads ``README.md`` alongside.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import git  # type: ignore

from app.core.catalog_index import detail_at_path as _detail_at_path, discover as _discover, read_readme
from app.core.db import get_session_factory
from app.core.errors import Range42Error, SourceUnreachableError
from app.core.project import _redact_authed_url, authed_url
from app.core.models import Source, SourceRepo
from app.core.repository_urls import GIT_HTTP_ENV, require_repository_url
from app.schemas.v1.catalog import CatalogEntryDetail, CatalogEntrySummary
from app.schemas.v1.common import Page

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _clone_repo(src: Source, repo: SourceRepo, workdir: Path) -> Path:
    url = authed_url(
        require_repository_url(f"{str(src.base_url).rstrip('/')}/{repo.owner}/{repo.repo}.git"),
        src.token_ref,
    )
    dest = workdir / f"{src.id}-{repo.owner}-{repo.repo}"
    if not dest.exists():
        try:
            git.Repo.clone_from(url, str(dest), depth=1, branch=repo.branch, env=GIT_HTTP_ENV)
        except Exception as e:
            raise SourceUnreachableError(
                details=[
                    {
                        "field": "source_id",
                        "reason": _redact_authed_url(
                            f"{repo.owner}/{repo.repo}: {e}"),
                    }
                ]
            )
    return dest


def _entries_for_repo(src: Source, repo: SourceRepo) -> list[CatalogEntrySummary]:
    # The worker owns the checkout lifetime even if its request is cancelled.
    with tempfile.TemporaryDirectory() as td:
        dest = _clone_repo(src, repo, Path(td))
        with git.Repo(dest) as checkout:
            sha = checkout.head.commit.hexsha
        return [CatalogEntrySummary(source_id=src.id, sha=sha, **entry)
                for entry in _discover(dest)]


def _detail_for_repo(src: Source, repo: SourceRepo, path: str) -> CatalogEntryDetail | None:
    with tempfile.TemporaryDirectory() as td:
        dest = _clone_repo(src, repo, Path(td))
        entry = _detail_at_path(dest, path)
        if entry is None:
            return None
        with git.Repo(dest) as checkout:
            sha = checkout.head.commit.hexsha
        return CatalogEntryDetail(source_id=src.id, sha=sha, **entry,
                                  readme_md=read_readme(dest, entry["path"]))


@router.get("/entries", response_model=Page[CatalogEntrySummary])
async def list_entries(
    session: AsyncSession = Depends(_session),
    kind: str | None = Query(None),
    source_id: str | None = Query(None),
    tag: str | None = Query(None),
    offset: int = 0,
    limit: int = 100,
):
    sources_q = select(Source)
    if source_id:
        sources_q = sources_q.where(Source.id == source_id)
    sources = (await session.execute(sources_q)).scalars().all()
    summaries: list[CatalogEntrySummary] = []
    for src in sources:
        repos = (
            await session.execute(
                select(SourceRepo).where(SourceRepo.source_id == src.id)
            )
        ).scalars().all()
        for repo in repos:
            for entry in await asyncio.to_thread(_entries_for_repo, src, repo):
                if kind and entry.kind != kind:
                    continue
                if tag and tag not in entry.tags:
                    continue
                summaries.append(entry)
    summaries.sort(key=lambda entry: (entry.source_id, entry.path))
    total = len(summaries)
    return Page[CatalogEntrySummary](
        items=summaries[offset : offset + limit],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/entries/{source_id}/{path:path}", response_model=CatalogEntryDetail
)
async def get_entry(
    source_id: str, path: str, session: AsyncSession = Depends(_session)
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
    repos = (
        await session.execute(
            select(SourceRepo).where(SourceRepo.source_id == source_id)
        )
    ).scalars().all()
    for repo in repos:
        entry = await asyncio.to_thread(_detail_for_repo, src, repo, path)
        if entry is not None:
            return entry
    raise Range42Error(
        error="not_found",
        code="NOT_FOUND",
        status=404,
        message=f"Entry {path} not found in source {source_id}",
    )
