"""Retained gateway generation pins, separate from future participant NICs."""
from alembic import op
import sqlalchemy as sa

revision = '0012_gateway_foundations'
down_revision = '0011_native_environments'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('native_environments', sa.Column('foundation_json', sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM native_environments WHERE foundation_json IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Native gateway bindings exist; preserve gateway foundation records before downgrading.')
    with op.batch_alter_table('native_environments') as table:
        table.drop_column('foundation_json')
