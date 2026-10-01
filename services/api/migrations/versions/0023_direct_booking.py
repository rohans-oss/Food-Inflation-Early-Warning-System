"""direct farmer -> driver booking: driver availability, trip requests + offers, web-push subscriptions,
trips.booking_channel (backfilled: demo / farmer_company / fpo_fleet)

Revision ID: 0023
Revises: 0022
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("trips", sa.Column("booking_channel", sa.String(16)))
    op.create_index("ix_trips_booking_channel", "trips", ["booking_channel"])
    op.execute("UPDATE trips SET booking_channel = 'demo' WHERE is_simulated")
    op.execute("UPDATE trips SET booking_channel = 'farmer_company' WHERE booking_channel IS NULL AND shipment_id IN "
               "(SELECT shipment_id FROM transport_bookings)")
    op.execute("UPDATE trips SET booking_channel = 'fpo_fleet' WHERE booking_channel IS NULL")

    op.create_table(
        "driver_availability",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("online", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("district", sa.String(80)),
        sa.Column("vehicle_id", sa.Integer, sa.ForeignKey("vehicles.id")),
        sa.Column("last_seen_at", TS),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "driver_availability_mandis",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("driver_availability.user_id"), primary_key=True),
        sa.Column("mandi_id", sa.Integer, sa.ForeignKey("mandis.id"), primary_key=True),
    )
    op.create_table(
        "trip_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lot_id", sa.Integer, sa.ForeignKey("lots.id"), nullable=False, index=True),
        sa.Column("farmer_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("district", sa.String(80), nullable=False),
        sa.Column("mandi_id", sa.Integer, sa.ForeignKey("mandis.id"), nullable=False),
        sa.Column("load_tons", sa.Float, nullable=False),
        sa.Column("estimated_km", sa.Float),
        sa.Column("estimated_fare", sa.Float),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("reason", sa.String(200)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("notified_at", TS),
        sa.Column("expires_at", TS),
        sa.Column("decided_at", TS),
        sa.Column("accepted_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("trip_id", sa.Integer, sa.ForeignKey("trips.id")),
    )
    op.create_table(
        "trip_request_offers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("request_id", sa.Integer, sa.ForeignKey("trip_requests.id"), nullable=False, index=True),
        sa.Column("driver_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("vehicle_id", sa.Integer, sa.ForeignKey("vehicles.id")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("push_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notified_at", TS, nullable=False),
        sa.Column("seen_at", TS),
        sa.Column("responded_at", TS),
        sa.UniqueConstraint("request_id", "driver_id"),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("endpoint_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_ok_at", TS),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions")
    op.drop_table("trip_request_offers")
    op.drop_table("trip_requests")
    op.drop_table("driver_availability_mandis")
    op.drop_table("driver_availability")
    op.drop_index("ix_trips_booking_channel", "trips")
    op.drop_column("trips", "booking_channel")
