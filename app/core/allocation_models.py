"""Durable authoring leases; no API Project is needed for a local draft."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class AllocationReservation(Base):
    __tablename__ = "allocation_reservations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host_id: Mapped[str] = mapped_column(ForeignKey("proxmox_hosts.id", ondelete="CASCADE"), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_url: Mapped[str | None] = mapped_column(String(512))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assignments: Mapped[list] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeploymentAllocation(Base):
    """Nonexpiring assignments; retained even when attempts fail or guests stop."""
    __tablename__ = "deployment_allocations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), unique=True, nullable=False)
    project_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    host_id: Mapped[str] = mapped_column(ForeignKey("proxmox_hosts.id"), nullable=False)
    api_url: Mapped[str] = mapped_column(String(512), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    assignments: Mapped[list] = mapped_column(JSON, nullable=False)
    source_assignments: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
