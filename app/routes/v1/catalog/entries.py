"""/v1/catalog/entries — cross-source browse + single-entry detail.

Each request shallow-clones every registered SourceRepo into a tmpdir,
walks the trees for catalog manifests, and synthesises catalog entry
summaries. Three manifest formats are recognised:

* ``range42.yaml`` — native Range42 manifest (kind/name/description/tags
  read straight from the document).
* ``meta.json`` — container/CTF challenges with an ``x_range42`` namespace.
* ``meta/main.yml`` — Ansible Galaxy roles with a ``galaxy_info`` block.

Detail endpoint also reads ``README.md`` alongside.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ruamel.yaml import YAML
import git  # type: ignore

from app.core.db import get_session_factory
from app.core.errors import Range42Error, SourceUnreachableError
from app.core.project import _redact_authed_url, authed_url
from app.core.models import Source, SourceRepo
from app.schemas.v1.catalog import CatalogEntryDetail, CatalogEntrySummary
from app.schemas.v1.common import Page

router = APIRouter()
_yaml = YAML(typ="safe")
_log = logging.getLogger(__name__)


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _clone_repo(src: Source, repo: SourceRepo, workdir: Path) -> Path:
    url = authed_url(
        f"{str(src.base_url).rstrip('/')}/{repo.owner}/{repo.repo}.git",
        src.token_ref,
    )
    dest = workdir / f"{src.id}-{repo.owner}-{repo.repo}"
    if not dest.exists():
        try:
            git.Repo.clone_from(url, str(dest), depth=1, branch=repo.branch)
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


def _entry_from_range42_yaml(p: Path, repo_dir: Path) -> dict | None:
    try:
        doc = _yaml.load(p.read_text()) or {}
    except Exception as e:
        _log.warning("skipping malformed range42.yaml %s: %s", p, e)
        return None
    rel = p.parent.relative_to(repo_dir).as_posix() or "."
    return {
        "path": rel,
        "kind": doc.get("kind", "unknown"),
        "name": doc.get("name", rel),
        "description": doc.get("description"),
        "tags": doc.get("tags") or [],
    }


def _entry_from_meta_json(p: Path, repo_dir: Path) -> dict | None:
    try:
        doc = json.loads(p.read_text()) or {}
    except Exception as e:
        _log.warning("skipping malformed meta.json %s: %s", p, e)
        return None
    rel = p.parent.relative_to(repo_dir).as_posix() or "."
    x = (doc.get("x_range42") or {}) if isinstance(doc, dict) else {}
    exercise = x.get("exercise") or {}
    catalog = x.get("catalog") or {}
    vuln = x.get("vuln") or {}
    return {
        "path": rel,
        "kind": "container",
        "name": exercise.get("id") or p.parent.name,
        "description": vuln.get("title"),
        "tags": catalog.get("tags") or [],
    }


def _entry_from_meta_main_yml(p: Path, repo_dir: Path) -> dict | None:
    try:
        doc = _yaml.load(p.read_text()) or {}
    except Exception as e:
        _log.warning("skipping malformed meta/main.yml %s: %s", p, e)
        return None
    role_dir = p.parent.parent  # parent of the `meta` directory
    rel = role_dir.relative_to(repo_dir).as_posix() or "."
    galaxy = (doc.get("galaxy_info") or {}) if isinstance(doc, dict) else {}
    return {
        "path": rel,
        "kind": "ansible_role",
        "name": role_dir.name,
        "description": galaxy.get("description"),
        "tags": galaxy.get("galaxy_tags") or [],
    }


def _detail_at_path(repo_dir: Path, path: str) -> dict | None:
    """Resolve a single entry at ``path`` across the three manifest formats.

    Mirrors :func:`_discover`'s recognition so the detail endpoint surfaces
    every entry the browse endpoint lists — not just ``range42.yaml``.
    Returns the summary dict plus a parsed ``document``, or ``None`` if no
    recognised manifest exists at ``path``.
    """
    base = repo_dir / path
    candidates = [
        (base / "range42.yaml", _entry_from_range42_yaml, _yaml.load),
        (base / "meta.json", _entry_from_meta_json, json.loads),
        (base / "meta" / "main.yml", _entry_from_meta_main_yml, _yaml.load),
    ]
    for manifest, parse_entry, load_doc in candidates:
        if not manifest.exists():
            continue
        entry = parse_entry(manifest, repo_dir)
        if entry is None:
            continue
        doc = load_doc(manifest.read_text())
        # CatalogEntryDetail.document is typed dict; a syntactically valid but
        # non-mapping top level (e.g. a YAML/JSON list) would otherwise fail
        # response validation. Coerce it to {} — the entry still resolves.
        if not isinstance(doc, dict):
            doc = {}
        return {**entry, "document": doc}
    return None


def _discover(repo_dir: Path) -> list[dict]:
    """Walk ``repo_dir`` for the three known manifest types.

    Returns a list of synthesised entry dicts with keys
    ``path``, ``kind``, ``name``, ``description``, ``tags`` — ready to
    feed into :class:`CatalogEntrySummary`.
    Malformed manifests are logged at WARNING and skipped.
    """
    out: list[dict] = []
    for p in repo_dir.rglob("range42.yaml"):
        entry = _entry_from_range42_yaml(p, repo_dir)
        if entry is not None:
            out.append(entry)
    for p in repo_dir.rglob("meta.json"):
        entry = _entry_from_meta_json(p, repo_dir)
        if entry is not None:
            out.append(entry)
    for p in repo_dir.rglob("meta/main.yml"):
        entry = _entry_from_meta_main_yml(p, repo_dir)
        if entry is not None:
            out.append(entry)
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
                for entry in _discover(dest):
                    if kind and entry["kind"] != kind:
                        continue
                    if tag and tag not in (entry.get("tags") or []):
                        continue
                    summaries.append(
                        CatalogEntrySummary(
                            source_id=src.id,
                            path=entry["path"],
                            kind=entry["kind"],
                            name=entry["name"],
                            description=entry.get("description"),
                            tags=entry.get("tags") or [],
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
            entry = _detail_at_path(dest, path)
            if entry is not None:
                readme = dest / path / "README.md"
                return CatalogEntryDetail(
                    source_id=src.id,
                    path=entry["path"],
                    kind=entry["kind"],
                    name=entry["name"],
                    description=entry.get("description"),
                    tags=entry.get("tags") or [],
                    sha=None,
                    document=entry["document"],
                    readme_md=readme.read_text() if readme.exists() else None,
                )
    raise Range42Error(
        error="not_found",
        code="NOT_FOUND",
        status=404,
        message=f"Entry {path} not found in source {source_id}",
    )
