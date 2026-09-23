"""Persist the exact project revision used by each attempt.

Revision ID: 0003_attempt_project_sha
Revises: 0002_proxmox_host_unique_name
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_attempt_project_sha"
down_revision = "0002_proxmox_host_unique_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("project_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("attempts") as batch:
        batch.drop_column("project_sha")
