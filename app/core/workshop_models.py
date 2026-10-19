"""Run-local private participant material, encrypted with the installation key."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.credential_store import EncryptedCredential
from app.core.models import Base


class WorkshopRun(Base):
    __tablename__ = 'workshop_runs'
    deployment_id: Mapped[str] = mapped_column(ForeignKey('deployments.id'), primary_key=True)
    credentials: Mapped[str] = mapped_column(EncryptedCredential(), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default='draft')
