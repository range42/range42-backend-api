"""Catalog source + entry request/response DTOs."""
from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.core.repository_urls import validate_repository_url


class SourceRepoIn(BaseModel):
    owner: str = Field(min_length=1, max_length=128)
    repo: str = Field(min_length=1, max_length=128)
    branch: str = Field(default="main", min_length=1, max_length=128)

    @field_validator("owner", "repo")
    @classmethod
    def validate_repository_path(cls, value: str, info) -> str:
        # Namespaces may contain GitLab subgroups; repository names may not.
        parts = value.split("/") if info.field_name == "owner" else [value]
        if any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) for part in parts):
            raise ValueError("Use repository path segments without traversal or URL characters")
        if info.field_name == "repo" and value.endswith(".git"):
            raise ValueError("Use the repository name without the .git suffix")
        return value

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        # Equivalent structural restrictions to a Git branch ref, without
        # spawning a process during request validation.
        if (
            value.startswith("-") or value == "@" or ".." in value or "@{" in value
            or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
            or any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in value.split("/"))
        ):
            raise ValueError("Use a valid Git branch name")
        return value


class SourceRepoOut(BaseModel):
    id: str
    owner: str
    repo: str
    branch: str
    last_refreshed_at: datetime | None = None


class SourceCredentialsIn(BaseModel):
    auth_kind: str = Field(pattern="^(pat|none)$")
    token_ref: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_credentials(self):
        if self.auth_kind == "pat" and not (self.token_ref or "").strip():
            raise ValueError("A personal access token is required for PAT authentication")
        if self.auth_kind == "none":
            self.token_ref = None
        return self


class SourceIn(BaseModel):
    provider: str = Field(pattern="^(github|gitlab|gitea|generic)$")
    base_url: HttpUrl
    auth_kind: str = Field(pattern="^(pat|ssh|none)$")
    token_ref: str | None = Field(default=None, max_length=128)
    # Entries are addressed by source + path, so each newly registered source
    # must identify one repository. Empty sources remain valid for legacy use.
    repos: list[SourceRepoIn] = Field(default_factory=list, max_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: HttpUrl) -> HttpUrl:
        validate_repository_url(str(value))
        return value

    @model_validator(mode="after")
    def validate_credentials(self):
        if self.auth_kind in {"pat", "none"}:
            credentials = SourceCredentialsIn(auth_kind=self.auth_kind, token_ref=self.token_ref)
            self.token_ref = credentials.token_ref
        return self


class SourceOut(BaseModel):
    id: str
    provider: str
    base_url: HttpUrl
    auth_kind: str
    # The PAT itself is never returned; expose only whether one is stored.
    has_token: bool = False
    created_at: datetime
    repos: list[SourceRepoOut] = Field(default_factory=list)


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
