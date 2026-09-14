"""Persist attributed mutation intents and HTTP outcomes."""
from alembic import op
import sqlalchemy as sa

revision = '0008_audit_records'
down_revision = '0007_snapshot_sets'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_records',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('method', sa.String(16), nullable=False),
        sa.Column('route', sa.String(256), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('status_code', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_audit_records_created_at', 'audit_records', ['created_at'])


def downgrade() -> None:
    if op.get_bind().execute(sa.text('SELECT 1 FROM audit_records LIMIT 1')).first():
        raise RuntimeError('Audit records exist; preserve them before a reviewed offline downgrade.')
    op.drop_table('audit_records')
