"""/v1/catalog/entries — cross-source browse + single-entry detail.

Each request shallow-clones every registered SourceRepo into a tmpdir,
walks the trees for ``range42.yaml`` manifests, and synthesises catalog
entry summaries. Detail endpoint also reads ``README.md`` alongside.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ruamel.yaml import YAML
import git  # type: ignore

from app.core.db import get_session_factory
from app.core.errors import Range42Error, SourceUnreachableError
from app.core.models import Source, SourceRepo
from app.schemas.v1.catalog import CatalogEntryDetail, CatalogEntrySummary
from app.schemas.v1.common import Page

router = APIRouter()
_yaml = YAML(typ="safe")


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _clone_repo(src: Source, repo: SourceRepo, workdir: Path) -> Path:
    url = f"{str(src.base_url).rstrip('/')}/{repo.owner}/{repo.repo}.git"
    dest = workdir / f"{src.id}-{repo.owner}-{repo.repo}"
    if not dest.exists():
        try:
            git.Repo.clone_from(url, str(dest), depth=1, branch=repo.branch)
        except Exception as e:
            raise SourceUnreachableError(
                details=[
                    {
                        "field": "source_id",
                        "reason": f"{repo.owner}/{repo.repo}: {e}",
                    }
                ]
            )
    return dest


def _discover(repo_dir: Path) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for p in repo_dir.rglob("range42.yaml"):
        try:
            doc = _yaml.load(p.read_text())
            rel = p.parent.relative_to(repo_dir).as_posix() or "."
            out.append((rel, doc or {}))
        except Exception:
            continue
    return out


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
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        for src in sources:
            repos = (
                await session.execute(
                    select(SourceRepo).where(SourceRepo.source_id == src.id)
                )
            ).scalars().all()
            for repo in repos:
                dest = _clone_repo(src, repo, workdir)
                for rel, doc in _discover(dest):
                    if kind and doc.get("kind") != kind:
                        continue
                    if tag and tag not in (doc.get("tags") or []):
                        continue
                    summaries.append(
                        CatalogEntrySummary(
                            source_id=src.id,
                            path=rel,
                            kind=doc.get("kind", "unknown"),
                            name=doc.get("name", rel),
                            description=doc.get("description"),
                            tags=doc.get("tags", []),
                            sha=None,
                        )
                    )
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
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        for repo in repos:
            dest = _clone_repo(src, repo, workdir)
            manifest = dest / path / "range42.yaml"
            if manifest.exists():
                doc = _yaml.load(manifest.read_text()) or {}
                readme = dest / path / "README.md"
                return CatalogEntryDetail(
                    source_id=src.id,
                    path=path,
                    kind=doc.get("kind", "unknown"),
                    name=doc.get("name", path),
                    description=doc.get("description"),
                    tags=doc.get("tags", []),
                    sha=None,
                    document=doc,
                    readme_md=readme.read_text() if readme.exists() else None,
                )
    raise Range42Error(
        error="not_found",
        code="NOT_FOUND",
        status=404,
        message=f"Entry {path} not found in source {source_id}",
    )
