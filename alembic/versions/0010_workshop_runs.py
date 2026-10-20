"""Persist run-local encrypted workshop material separately from public metadata."""
from alembic import op
import sqlalchemy as sa

revision = '0010_workshop_runs'
down_revision = '0009_native_contexts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workshop_runs',
        sa.Column('deployment_id', sa.String(64), sa.ForeignKey('deployments.id'), primary_key=True),
        sa.Column('credentials', sa.Text(), nullable=False),
        sa.Column('phase', sa.String(32), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM workshop_runs LIMIT 1')).first():
        raise RuntimeError('Workshop runs exist; preserve participant material before downgrading.')
    op.drop_table('workshop_runs')
