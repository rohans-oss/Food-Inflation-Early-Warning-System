"""delivery receipts (proof of delivery & sale)

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.add_column(sa.Column("receipt_no", sa.String(32), nullable=True))
        b.add_column(sa.Column("receipt_token", sa.String(64), nullable=True))
        b.create_unique_constraint("uq_lots_receipt_token", ["receipt_token"])


def downgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.drop_constraint("uq_lots_receipt_token", type_="unique")
        b.drop_column("receipt_token")
        b.drop_column("receipt_no")
