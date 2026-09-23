"""Project CRUD + compose/validate DTOs."""
from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.repository_urls import validate_repository_url


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    source_id: str | None = Field(default=None, max_length=64)
    branch_strategy: str | None = Field(default=None, pattern="^(shared_repo_subdir|dedicated_repo)$")
    repo_owner: str | None = Field(default=None, max_length=128)
    repo_name: str | None = Field(default=None, max_length=128)
    subdir: str | None = Field(default=None, max_length=256)
    base_catalog_url: str | None = Field(default=None, max_length=512)
    base_catalog_sha: str | None = Field(default=None, max_length=64)

    @field_validator("base_catalog_url")
    @classmethod
    def approved_catalog_url(cls, value):
        return validate_repository_url(value) if value else value

    @field_validator("name", "source_id", "branch_strategy")
    @classmethod
    def required_text(cls, value):
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("repo_owner", "repo_name")
    @classmethod
    def repository_part(cls, value, info):
        if value is None:
            return None
        parts = value.split("/") if info.field_name == "repo_owner" else [value]
        if any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part)
               or part in {".", ".."} for part in parts):
            raise ValueError("must be a repository name or namespace")
        if info.field_name == "repo_name" and value.endswith(".git"):
            raise ValueError("repository name must omit the .git suffix")
        return value

    @field_validator("subdir")
    @classmethod
    def safe_subdir(cls, value):
        if value in (None, "", "."):
            return None
        if (value.startswith("/") or "\\" in value
                or any(ord(char) < 32 for char in value)
                or any(part in {"", ".", "..", ".git"} for part in value.split("/"))):
            raise ValueError("must be a safe relative repository directory")
        return value


class ProjectIn(ProjectPatch):
    name: str = Field(max_length=256)
    source_id: str = Field(max_length=64)
    branch_strategy: str = Field(pattern="^(shared_repo_subdir|dedicated_repo)$")

    @model_validator(mode="after")
    def repository_pair(self):
        if bool(self.repo_owner) != bool(self.repo_name):
            raise ValueError("repo_owner and repo_name must be supplied together")
        return self


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
