"""Durable HTTP mutation intents; never store request bodies, URLs or headers."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, Integer, String, select, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core import db
from app.core.models import Base


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    route: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def begin_record(principal, method, route):
    row = AuditRecord(id=uuid.uuid4().hex, actor_id=principal.actor_id, role=principal.role,
                      method=method, route=route, state="started", created_at=datetime.now(timezone.utc))
    async with db.get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row.id


async def finish_record(record_id, status_code, *, denied=False):
    async with db.get_session_factory()() as session:
        row = await session.get(AuditRecord, record_id)
        row.status_code = status_code
        row.state = "denied" if denied else "completed"
        row.finished_at = datetime.now(timezone.utc)
        await session.commit()


async def list_records(offset, limit):
    async with db.get_session_factory()() as session:
        total = await session.scalar(select(func.count()).select_from(AuditRecord))
        rows = (await session.scalars(select(AuditRecord).order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc()).offset(offset).limit(limit))).all()
        return {"total": total, "items": [{key: getattr(row, key) for key in (
            "id", "actor_id", "role", "method", "route", "state", "status_code", "created_at", "finished_at")}
            for row in rows]}
