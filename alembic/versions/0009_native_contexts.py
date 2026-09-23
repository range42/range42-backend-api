"""Bind native deployments to their saved scenario and existing context."""
from alembic import op
import sqlalchemy as sa

revision = "0009_native_contexts"
down_revision = "0008_audit_records"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deployments", sa.Column("native", sa.JSON(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM deployments WHERE native IS NOT NULL AND native != 'null' LIMIT 1")).first():
        raise RuntimeError("Native deployments exist; preserve them before downgrading.")
    op.drop_column("deployments", "native")
