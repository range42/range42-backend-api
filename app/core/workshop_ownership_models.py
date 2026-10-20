"""Retain both sides of original generation adoption before enabling dispatch."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class WorkshopOwnershipImport(Base):
    __tablename__ = "workshop_ownership_imports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id"), unique=True, nullable=False
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    review_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    ownership_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    witness_json: Mapped[str] = mapped_column(Text, nullable=False)
    # This identifier remains as a tombstone after verified allocation release.
    allocation_id: Mapped[str | None] = mapped_column(String(64))
    controller_receipt_json: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkshopLegacyDispatch(Base):
    """Retain queue-job identity even when its attempt has already ended."""

    __tablename__ = "workshop_legacy_dispatches"
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id"), primary_key=True
    )
    dispatch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id"), unique=True, nullable=False
    )
    operation: Mapped[dict] = mapped_column(JSON, nullable=False)
