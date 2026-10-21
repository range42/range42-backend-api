"""Retain original bounded preparation requests on the existing capacity hold."""
from alembic import op
import sqlalchemy as sa

revision = '0020_native_preparation'
down_revision = '0019_native_runtime_target'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('capacity_holds', sa.Column('preparation_request_json', sa.Text(), nullable=True))
    op.add_column('capacity_holds', sa.Column('preparation_receipt_json', sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM capacity_holds WHERE preparation_request_json IS NOT NULL OR preparation_receipt_json IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Native preparation records exist; preserve original ownership before downgrade.')
    with op.batch_alter_table('capacity_holds') as batch:
        batch.drop_column('preparation_receipt_json')
        batch.drop_column('preparation_request_json')
