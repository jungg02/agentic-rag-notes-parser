"""add is_ocr to chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("is_ocr", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("chunks", "is_ocr")
