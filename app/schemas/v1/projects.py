"""Project CRUD + compose/validate DTOs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    name: str
    source_id: str
    branch_strategy: str = Field(pattern="^(shared_repo_subdir|dedicated_repo)$")
    repo_owner: str | None = None
    repo_name: str | None = None
    subdir: str | None = None
    base_catalog_url: str | None = None
    base_catalog_sha: str | None = None


class ProjectOut(ProjectIn):
    id: str
    created_at: datetime
    updated_at: datetime


class ComposeRequest(BaseModel):
    team_count: int | None = None


class ComposeResponse(BaseModel):
    effective_doc: dict
    effective_doc_hash: str


class ValidateResponse(BaseModel):
    ok: bool
    errors: list[dict] = []
