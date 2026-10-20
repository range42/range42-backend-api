"""Immutable native environment versions; credentials remain on ProxmoxHost."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.models import Base, _utcnow


class NativeEnvironment(Base):
    __tablename__ = 'native_environments'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    base_context_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_host_id: Mapped[str] = mapped_column(ForeignKey('proxmox_hosts.id'), nullable=False)
    target_node: Mapped[str] = mapped_column(String(128), nullable=False)
    target_api_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    foundation_json: Mapped[str | None] = mapped_column(Text)
    review_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
