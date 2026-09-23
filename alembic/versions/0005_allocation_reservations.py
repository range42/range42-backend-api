"""Persist expiring VMID and configured-address authoring reservations.

Revision ID: 0005_allocation_reservations
Revises: 0004_runtime_operations
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_allocation_reservations"
down_revision = "0004_runtime_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allocation_reservations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_key", sa.String(128), nullable=False, unique=True),
        sa.Column("host_id", sa.String(64), sa.ForeignKey("proxmox_hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("assignments", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_allocation_reservations_expires_at", "allocation_reservations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_allocation_reservations_expires_at", table_name="allocation_reservations")
    op.drop_table("allocation_reservations")
