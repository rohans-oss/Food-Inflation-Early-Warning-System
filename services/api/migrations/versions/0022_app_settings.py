"""app settings (server-generated values that must survive restarts, e.g. the session signing key)

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("app_settings", sa.Column("key", sa.String(64), primary_key=True),
                    sa.Column("value", sa.Text, nullable=False))


def downgrade() -> None:
    op.drop_table("app_settings")
