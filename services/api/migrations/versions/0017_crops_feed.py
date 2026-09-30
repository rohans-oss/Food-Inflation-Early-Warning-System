"""custom crops + commodities seen in the live Agmarknet feed

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_crops",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
        sa.Column("feed_name", sa.String(80), nullable=True),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "feed_commodities",
        sa.Column("name", sa.String(80), primary_key=True),
        sa.Column("raw_name", sa.String(80), nullable=False),
        sa.Column("first_seen", sa.Date, nullable=False),
        sa.Column("last_seen", sa.Date, nullable=False),
        sa.Column("last_rows", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("feed_commodities")
    op.drop_table("custom_crops")
