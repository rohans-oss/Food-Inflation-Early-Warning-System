"""farmer transport bookings + recorded payments

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transport_bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("lots.id"), nullable=False),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("farmer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fleet_org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("mandi_id", sa.Integer(), sa.ForeignKey("mandis.id"), nullable=False),
        sa.Column("pickup_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fare_estimate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id"), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_transport_bookings_lot_id", "transport_bookings", ["lot_id"])
    op.create_index("ix_transport_bookings_shipment_id", "transport_bookings", ["shipment_id"])
    op.create_index("ix_transport_bookings_fleet_org_id", "transport_bookings", ["fleet_org_id"])
    with op.batch_alter_table("lots") as b:
        b.add_column(sa.Column("payment_method", sa.String(20), nullable=True))
        b.add_column(sa.Column("payment_ref", sa.String(80), nullable=True))
        b.add_column(sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("payment_received_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lots") as b:
        for c in ("payment_received_at", "paid_at", "payment_ref", "payment_method"):
            b.drop_column(c)
    op.drop_table("transport_bookings")
