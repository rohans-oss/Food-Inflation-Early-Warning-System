"""lot preferred mandi (the farmer's choice)

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.add_column(sa.Column("preferred_mandi_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_lots_preferred_mandi", "mandis", ["preferred_mandi_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("lots") as b:
        b.drop_constraint("fk_lots_preferred_mandi", type_="foreignkey")
        b.drop_column("preferred_mandi_id")
