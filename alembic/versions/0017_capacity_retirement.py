"""Preserve pre-RPC request identity and verified unused-intent retirement."""
from alembic import op
import sqlalchemy as sa

revision = '0017_capacity_retirement'
down_revision = '0016_capacity_holds'
branch_labels = None
depends_on = None


def upgrade():
    for name, size in [('native_request_id', 32), ('native_request_actor', 128),
                       ('retired_by', 128), ('retirement_approval_revision', 64)]:
        op.add_column('capacity_holds', sa.Column(name, sa.String(size), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM capacity_holds WHERE native_request_id IS NOT NULL OR native_request_actor IS NOT NULL OR retired_by IS NOT NULL OR retirement_approval_revision IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Native capacity lifecycle audit exists; preserve original dispatch and retirement records before downgrade.')
    with op.batch_alter_table('capacity_holds') as batch:
        for name in ['native_request_id', 'native_request_actor', 'retired_by', 'retirement_approval_revision']:
            batch.drop_column(name)
