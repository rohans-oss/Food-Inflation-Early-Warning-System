"""payment details recorded with a payment (bank account masked to last 4 digits, IFSC, branch; UPI ID)

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.add_column(sa.Column("payment_details", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.drop_column("payment_details")
