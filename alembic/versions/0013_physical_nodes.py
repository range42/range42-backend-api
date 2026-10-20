"""Retained physical nodes and reviewed API aliases; no capacity grants."""

from alembic import op
import sqlalchemy as sa

revision = "0013_physical_nodes"
down_revision = "0012_gateway_foundations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "physical_nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("hardware_binding", sa.String(64), nullable=False, unique=True),
        sa.Column("node_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "physical_node_bindings",
        sa.Column(
            "host_id",
            sa.String(64),
            sa.ForeignKey("proxmox_hosts.id"),
            primary_key=True,
        ),
        sa.Column(
            "physical_node_id",
            sa.String(32),
            sa.ForeignKey("physical_nodes.id"),
            nullable=False,
        ),
        sa.Column("target_revision", sa.String(64), nullable=False),
        sa.Column("agent_revision", sa.String(64), nullable=False),
        sa.Column("review_revision", sa.String(64), nullable=False),
        sa.Column("observation_digest", sa.String(64), nullable=False),
        sa.Column("observation_json", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    if (
        op.get_bind().execute(sa.text("SELECT 1 FROM physical_nodes LIMIT 1")).first()
        or op.get_bind()
        .execute(sa.text("SELECT 1 FROM physical_node_bindings LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "Physical identity records exist; retain their bindings and commitments before downgrade."
        )
    op.drop_table("physical_node_bindings")
    op.drop_table("physical_nodes")
