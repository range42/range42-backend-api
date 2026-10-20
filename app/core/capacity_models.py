"""Nonexpiring canonical capacity intents, retained across uncertain outcomes."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class CapacityHold(Base):
    __tablename__ = "capacity_holds"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Historical identity survives retirement/deletion of the allocation. Never
    # cascade a capacity commitment away with a deployment or draft lease.
    deployment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    allocation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    physical_node_id: Mapped[str] = mapped_column(
        ForeignKey("physical_nodes.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    root_policy_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
