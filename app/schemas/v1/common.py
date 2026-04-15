"""Shared pagination + error-detail DTOs for /v1."""
from __future__ import annotations

from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: Sequence[T]
    total: int
    offset: int = 0
    limit: int = 100


class ErrorDetail(BaseModel):
    field: str
    reason: str
    hint: str | None = None
