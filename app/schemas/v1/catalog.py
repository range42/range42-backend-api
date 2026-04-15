"""Catalog source + entry request/response DTOs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class SourceIn(BaseModel):
    provider: str = Field(pattern="^(github|gitlab|gitea|generic)$")
    base_url: HttpUrl
    auth_kind: str = Field(pattern="^(pat|ssh|none)$")
    token_ref: str | None = None


class SourceOut(BaseModel):
    id: str
    provider: str
    base_url: HttpUrl
    auth_kind: str
    token_ref: str | None = None
    created_at: datetime


class SourceRefreshResult(BaseModel):
    source_id: str
    repos_seen: int
    entries_indexed: int
    started_at: datetime
    finished_at: datetime


class CatalogEntrySummary(BaseModel):
    source_id: str
    path: str
    kind: str
    name: str
    description: str | None = None
    tags: list[str] = []
    sha: str | None = None


class CatalogEntryDetail(CatalogEntrySummary):
    document: dict
    readme_md: str | None = None
