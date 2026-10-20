"""Retained admission decisions, independent of attempt success or process life."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class DeploymentAdmission(Base):
    __tablename__ = "deployment_admissions"
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    pause_id: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    last_request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    last_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
