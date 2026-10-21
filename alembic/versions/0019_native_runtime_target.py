"""Retain the physical identity of an explicitly reused native runtime target."""
from alembic import op
import sqlalchemy as sa

revision = '0019_native_runtime_target'
down_revision = '0018_native_capacity_receipt'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('native_environments', sa.Column('runtime_target_json', sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM native_environments WHERE runtime_target_json IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Reviewed runtime target pins exist; preserve their original identities before downgrade.')
    with op.batch_alter_table('native_environments') as batch:
        batch.drop_column('runtime_target_json')
