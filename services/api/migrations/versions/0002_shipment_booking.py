"""shipment booking: which fleet a shipment is booked with, and when

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shipments") as b:
        b.add_column(sa.Column("fleet_org_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key("fk_shipments_fleet_org_id", "organizations", ["fleet_org_id"], ["id"])
        b.create_index("ix_shipments_fleet_org_id", ["fleet_org_id"])


def downgrade() -> None:
    with op.batch_alter_table("shipments") as b:
        b.drop_index("ix_shipments_fleet_org_id")
        b.drop_constraint("fk_shipments_fleet_org_id", type_="foreignkey")
        b.drop_column("booked_at")
        b.drop_column("fleet_org_id")
