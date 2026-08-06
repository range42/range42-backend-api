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


class ErrorEnvelope(BaseModel):
    """What every v1 error actually returns.

    Mirrors ``app.core.errors._envelope``. Declared so the generated spec
    stops advertising FastAPI's default ``{"detail": [...]}`` for 422s —
    clients built from the committed spec were deserialising the wrong
    shape for every validation failure.
    """

    error: str
    message: str
    code: str
    details: list[ErrorDetail] = []
    trace_id: str
    timestamp: str
