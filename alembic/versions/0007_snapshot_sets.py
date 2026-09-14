"""Persist reviewed snapshot plans and per-member native task outcomes."""

from alembic import op
import sqlalchemy as sa

revision = "0007_snapshot_sets"
down_revision = "0006_deployment_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshot_sets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("deployments.id"),
            nullable=False,
        ),
        sa.Column("project_sha", sa.String(64), nullable=False),
        sa.Column(
            "host_id", sa.String(64), sa.ForeignKey("proxmox_hosts.id"), nullable=False
        ),
        sa.Column("target_digest", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("native_name", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_snapshot_sets_deployment_id", "snapshot_sets", ["deployment_id"]
    )
    op.create_table(
        "snapshot_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "set_id", sa.String(32), sa.ForeignKey("snapshot_sets.id"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column(
            "attempt_id", sa.String(64), sa.ForeignKey("attempts.id"), unique=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_snapshot_operations_set_id", "snapshot_operations", ["set_id"])


def downgrade() -> None:
    # A populated native-task journal is operational ownership, not disposable
    # cache. Reverting it would hide remote work or discard recovery evidence.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM snapshot_sets LIMIT 1")).first():
        raise RuntimeError(
            "Snapshot history exists; preserve the journal before a reviewed offline downgrade."
        )
    op.drop_table("snapshot_operations")
    op.drop_table("snapshot_sets")
