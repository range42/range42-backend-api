"""Retain physical identity independently of API aliases and credential rotation."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class PhysicalNode(Base):
    __tablename__ = "physical_nodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    hardware_binding: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PhysicalNodeBinding(Base):
    __tablename__ = "physical_node_bindings"
    host_id: Mapped[str] = mapped_column(
        ForeignKey("proxmox_hosts.id"), primary_key=True
    )
    physical_node_id: Mapped[str] = mapped_column(
        ForeignKey("physical_nodes.id"), nullable=False
    )
    target_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    review_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_json: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
