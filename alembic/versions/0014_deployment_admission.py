"""Retain managed-writer pauses independently of runner/process lifetime."""

from alembic import op
import sqlalchemy as sa

revision = "0014_deployment_admission"
down_revision = "0013_physical_nodes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_admissions",
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("deployments.id"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("pause_id", sa.String(32), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("last_request_id", sa.String(32), nullable=False),
        sa.Column("last_request_digest", sa.String(64), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM deployment_admissions LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "Retained deployment admission decisions exist; preserve their recovery state before downgrade."
        )
    op.drop_table("deployment_admissions")
