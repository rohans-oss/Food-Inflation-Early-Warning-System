"""pickup code: the driver tells the farmer a 4-digit code; the farmer enters it to confirm the handover

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trips") as b:
        b.add_column(sa.Column("pickup_code", sa.String(6), nullable=True))
        b.add_column(sa.Column("pickup_code_failures", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("trips") as b:
        b.drop_column("pickup_code_failures")
        b.drop_column("pickup_code")
