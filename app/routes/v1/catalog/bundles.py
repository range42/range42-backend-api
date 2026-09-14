"""Resolve a selected source revision into an executable installed VM bundle."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bundle_attachments import resolve_bundle
from app.core.credential_store import resolve_git_credential
from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Source, SourceRepo
from app.core.project import checkout_repository
from app.core.repository_urls import require_repository_url

router = APIRouter()


class BundleResolveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=512, pattern=r'^bundles/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$')
    sha: str = Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
    target_kind: Literal['VM']

    @field_validator('path')
    @classmethod
    def contained_path(cls, value: str) -> str:
        if any(part in {'.', '..'} for part in value.split('/')):
            raise ValueError('Bundle path cannot contain traversal segments')
        return value


async def _session():
    async with get_session_factory()() as session:
        yield session


def _resolve_source_bundle(source: Source, repo: SourceRepo, request: BundleResolveRequest) -> dict:
    url = require_repository_url(f"{source.base_url.rstrip('/')}/{repo.owner}/{repo.repo}.git")
    # Worker owns the complete checkout lifetime, including cancellation cleanup.
    with tempfile.TemporaryDirectory() as directory:
        root = checkout_repository(repo_url=url, sha=request.sha, dest=Path(directory) / 'source',
                                   token=resolve_git_credential(source.token_ref) if source.auth_kind == 'pat' else None)
        return resolve_bundle(root, source_id=source.id, sha=request.sha, path=request.path)


@router.post('/sources/{source_id}/bundles/resolve')
async def resolve_source_bundle(source_id: str, request: BundleResolveRequest,
                                session: AsyncSession = Depends(_session)):
    source = await session.get(Source, source_id)
    if source is None:
        raise Range42Error(code='NOT_FOUND', error='not_found', status=404, message='Bundle source not found')
    repos = (await session.execute(select(SourceRepo).where(SourceRepo.source_id == source_id))).scalars().all()
    if len(repos) != 1 or source.auth_kind not in {'pat', 'none'}:
        raise Range42Error(code='BUNDLE_SOURCE_UNSUPPORTED', error='bundle_source_unsupported', status=409,
                           message='Bundle resolution requires one repository per source using HTTPS with PAT or public access')
    return await asyncio.to_thread(_resolve_source_bundle, source, repos[0], request)
