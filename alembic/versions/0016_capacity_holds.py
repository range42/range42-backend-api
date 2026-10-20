"""Retain canonical capacity intents through uncertain remote outcomes."""

from alembic import op
import sqlalchemy as sa

revision = "0016_capacity_holds"
down_revision = "0015_workshop_ownership"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "capacity_holds",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("deployment_id", sa.String(64), unique=True, nullable=False),
        sa.Column("allocation_id", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "physical_node_id",
            sa.String(32),
            sa.ForeignKey("physical_nodes.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("plan_revision", sa.String(64), nullable=False),
        sa.Column("binding_revision", sa.String(64), nullable=False),
        sa.Column("agent_revision", sa.String(64), nullable=False),
        sa.Column("root_policy_revision", sa.String(64), nullable=False),
        sa.Column("approval_revision", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_capacity_holds_physical_node_id", "capacity_holds", ["physical_node_id"]
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM capacity_holds LIMIT 1")).first():
        raise RuntimeError(
            "Capacity commitments exist; preserve original capacity and native recovery state before downgrade."
        )
    op.drop_index("ix_capacity_holds_physical_node_id", table_name="capacity_holds")
    op.drop_table("capacity_holds")
