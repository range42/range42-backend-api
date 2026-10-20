"""Retain the independently observed original native capacity acceptance."""
from alembic import op
import sqlalchemy as sa

revision = '0018_native_capacity_receipt'
down_revision = '0017_capacity_retirement'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('capacity_holds', sa.Column('native_receipt_json', sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM capacity_holds WHERE native_receipt_json IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Native capacity receipts exist; preserve original acceptance before downgrade.')
    with op.batch_alter_table('capacity_holds') as batch:
        batch.drop_column('native_receipt_json')
