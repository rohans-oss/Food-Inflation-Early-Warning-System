"""trip tracking pause (Pre-V3 B-2)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # the driver's phone reported it stopped recording (browser app hidden); cleared by the next fix
    with op.batch_alter_table('trips', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tracking_paused_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('tracking_pause_reason', sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('trips', schema=None) as batch_op:
        batch_op.drop_column('tracking_pause_reason')
        batch_op.drop_column('tracking_paused_at')
