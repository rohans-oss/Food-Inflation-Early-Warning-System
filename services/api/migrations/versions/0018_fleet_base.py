"""fleet base location (where a transporter's trucks start from)

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("base_label", sa.String(120), nullable=True))
        b.add_column(sa.Column("base_lat", sa.Float, nullable=True))
        b.add_column(sa.Column("base_lon", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as b:
        b.drop_column("base_lon")
        b.drop_column("base_lat")
        b.drop_column("base_label")
