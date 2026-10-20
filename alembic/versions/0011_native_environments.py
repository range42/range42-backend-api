"""Administrator-reviewed native environment versions, without credentials."""
from alembic import op
import sqlalchemy as sa

revision = '0011_native_environments'
down_revision = '0010_workshop_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('native_environments',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('label', sa.String(128), nullable=False),
        sa.Column('base_context_id', sa.String(128), nullable=False),
        sa.Column('target_host_id', sa.String(64), sa.ForeignKey('proxmox_hosts.id'), nullable=False),
        sa.Column('target_node', sa.String(128), nullable=False),
        sa.Column('target_api_url', sa.String(2048), nullable=False),
        sa.Column('profile_json', sa.Text(), nullable=False),
        sa.Column('review_revision', sa.String(64), nullable=False),
        sa.Column('approved_by', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM native_environments LIMIT 1')).first():
        raise RuntimeError('Native environments exist; preserve deployment context bindings before downgrading.')
    op.drop_table('native_environments')
