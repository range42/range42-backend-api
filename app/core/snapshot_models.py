"""Reviewed snapshot sets and their per-member native operation journal."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class SnapshotSet(Base):
    __tablename__ = "snapshot_sets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id"), nullable=False, index=True
    )
    project_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    host_id: Mapped[str] = mapped_column(ForeignKey("proxmox_hosts.id"), nullable=False)
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    native_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    members: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SnapshotOperation(Base):
    __tablename__ = "snapshot_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_sets.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan: Mapped[dict] = mapped_column(JSON, nullable=False)
    members: Mapped[list] = mapped_column(JSON, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("attempts.id"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
