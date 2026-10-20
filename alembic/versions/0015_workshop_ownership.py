"""Preserve controller/backend ownership handoff through ambiguous replies."""

from alembic import op
import sqlalchemy as sa

revision = "0015_workshop_ownership"
down_revision = "0014_deployment_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workshop_ownership_imports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("deployments.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("review_revision", sa.String(64), nullable=False),
        sa.Column("ownership_revision", sa.String(64), nullable=False),
        sa.Column("binding_revision", sa.String(64), nullable=False),
        sa.Column("witness_json", sa.Text(), nullable=False),
        sa.Column("allocation_id", sa.String(64)),
        sa.Column("controller_receipt_json", sa.Text()),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workshop_legacy_dispatches",
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("deployments.id"),
            primary_key=True,
        ),
        sa.Column("dispatch_id", sa.String(128), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(64),
            sa.ForeignKey("attempts.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("operation", sa.JSON(), nullable=False),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM workshop_ownership_imports LIMIT 1"))
        .first()
        or op.get_bind()
        .execute(sa.text("SELECT 1 FROM workshop_legacy_dispatches LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "Original ownership handoffs exist; preserve both controller and backend recovery state before downgrade."
        )
    op.drop_table("workshop_legacy_dispatches")
    op.drop_table("workshop_ownership_imports")
