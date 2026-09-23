"""Persist guarded runtime operation intent and readback.

Revision ID: 0004_runtime_operations
Revises: 0003_attempt_project_sha
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_runtime_operations"
down_revision = "0003_attempt_project_sha"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("operation", sa.JSON(), nullable=True))
    op.add_column("attempts", sa.Column("operation_result", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("attempts") as batch:
        batch.drop_column("operation_result")
        batch.drop_column("operation")
