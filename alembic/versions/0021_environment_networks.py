"""Retain optional bridge ownership on its original registered environment."""
from alembic import op
import sqlalchemy as sa

revision = '0021_environment_networks'
down_revision = '0020_native_preparation'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('native_environments', sa.Column('network_preparation_json', sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM native_environments WHERE network_preparation_json IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Preserve original environment network ownership before downgrade.')
    with op.batch_alter_table('native_environments') as batch:
        batch.drop_column('network_preparation_json')
