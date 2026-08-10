"""SQLAlchemy 2.x declarative models for the v1 central state DB.

Column types match spec §8 state tables. All tables use string UUID ids
except WorkspaceLock (keyed by deployment_id).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    auth_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    token_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    repos: Mapped[list["SourceRepo"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class SourceRepo(Base):
    __tablename__ = "source_repos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    repo: Mapped[str] = mapped_column(String(128), nullable=False)
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="main")
    manifest_path: Mapped[str | None] = mapped_column(String(256))
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[Source] = relationship(back_populates="repos")
    __table_args__ = (UniqueConstraint("source_id", "owner", "repo", name="uq_source_repo"),)


class ProxmoxHost(Base):
    __tablename__ = "proxmox_hosts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_url: Mapped[str] = mapped_column(String(512), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    token_scope: Mapped[str | None] = mapped_column(String(256))
    default_bridge: Mapped[str] = mapped_column(String(32), default="vmbr0")
    protected_vmids_override_json: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_health_check_json: Mapped[str | None] = mapped_column(Text)
    # A host is identified by its name: the deploy bundle re-POSTs the same
    # name on every scenario run, and deployments.target_host_id is a FK here,
    # so a second row per re-run would strand earlier deployments on a host
    # nobody updates. Uniqueness turns those re-runs into in-place updates.
    __table_args__ = (UniqueConstraint("name", name="uq_proxmox_host_name"),)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    branch_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    repo_owner: Mapped[str | None] = mapped_column(String(128))
    repo_name: Mapped[str | None] = mapped_column(String(128))
    subdir: Mapped[str | None] = mapped_column(String(256))
    base_catalog_url: Mapped[str | None] = mapped_column(String(512))
    base_catalog_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    codename: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_label: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    target_host_id: Mapped[str] = mapped_column(ForeignKey("proxmox_hosts.id"), nullable=False)
    catalog_sha: Mapped[str | None] = mapped_column(String(64))
    project_sha: Mapped[str | None] = mapped_column(String(64))
    effective_doc_hash: Mapped[str | None] = mapped_column(String(80))
    team_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    current_attempt_id: Mapped[str | None] = mapped_column(String(64))
    workspace_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    __table_args__ = (UniqueConstraint("codename", "scenario_label", name="uq_deployment_workspace"),)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sub_reason: Mapped[str | None] = mapped_column(String(64))
    rc: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_cursor_tip: Mapped[int] = mapped_column(Integer, default=0)
    pid: Mapped[int | None] = mapped_column(Integer)
    artifact_dir: Mapped[str | None] = mapped_column(String(512))
    vault_hash: Mapped[str | None] = mapped_column(String(80))


class PreflightRecord(Base):
    __tablename__ = "preflight_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id"))
    vm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspaceLock(Base):
    __tablename__ = "workspace_locks"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    heartbeat_interval_s: Mapped[int] = mapped_column(Integer, default=30)
