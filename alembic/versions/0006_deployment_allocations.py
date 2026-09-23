"""Bind durable resource ownership to deployments, separately from draft leases."""
from alembic import op
import sqlalchemy as sa

revision = "0006_deployment_allocations"
down_revision = "0005_allocation_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("allocation_reservations", sa.Column("api_url", sa.String(512)))
    op.create_table(
        "deployment_allocations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("deployment_id", sa.String(64), sa.ForeignKey("deployments.id"), nullable=False, unique=True),
        sa.Column("project_sha", sa.String(64), nullable=False),
        sa.Column("host_id", sa.String(64), sa.ForeignKey("proxmox_hosts.id"), nullable=False),
        sa.Column("api_url", sa.String(512), nullable=False),
        sa.Column("node_name", sa.String(128), nullable=False),
        sa.Column("assignments", sa.JSON(), nullable=False),
        sa.Column("source_assignments", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deployment_allocations")
    op.drop_column("allocation_reservations", "api_url")
