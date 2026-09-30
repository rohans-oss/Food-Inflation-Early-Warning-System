"""All V1 core tables (project doc §12).

Coordinates are stored as plain lat/lon floats so the same models run on SQLite in
tests. On PostgreSQL the Alembic migration additionally adds PostGIS geography
columns + GiST indexes and turns gps_points into a TimescaleDB hypertable.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware on Postgres; SQLite drops tzinfo, so UTC is re-attached on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


# ---------------------------------------------------------------- identity


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")


class Organization(Base):
    """Tenants: FPOs, fleets, trading firms, buyers, lenders, government bodies."""

    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))  # fpo|fleet|trader|buyer|lender|government|platform
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"))
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    mandi_id: Mapped[int | None] = mapped_column(ForeignKey("mandis.id"))  # traders: their mandi
    preferred_lang: Mapped[str] = mapped_column(String(5), default="en")  # en|kn|hi
    watch_mandi_ids: Mapped[list] = mapped_column(JSON, default=list)  # buyers: mandis to watch
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    org: Mapped[Organization | None] = relationship()


class ScenarioRun(Base):
    """V3-2: one scenario-simulator run. COUNTERFACTUAL ESTIMATE — kept apart from `forecasts`, which a run never
    writes. Stores the parameters, the assumptions used (with sources), both channels' results and who ran it."""

    __tablename__ = "scenario_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario: Mapped[str] = mapped_column(String(32), index=True)  # rainfall_failure | export_ban
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    model_name: Mapped[str] = mapped_column(String(64))
    forecast_issue_date: Mapped[date | None] = mapped_column(Date)
    data_provenance: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(120))  # "COUNTERFACTUAL ESTIMATE — not a validated causal model"
    assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[list] = mapped_column(JSON, default=list)


class LoadProposal(Base):
    """V3-1: an optimizer proposal a person accepts or rejects. kind = consolidation (FPO: shared truckloads) or
    return_load (fleet owner: a job for a truck's drive home). Accepting creates shipments / trips through the normal
    checked paths and writes audit_log; a proposal is decided once."""

    __tablename__ = "load_proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # consolidation | return_load
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)  # FPO or fleet: the data scope
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed | accepted | rejected | stale
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    est_saving: Mapped[float] = mapped_column(Float, default=0.0)  # Rs, vs the no-sharing / drive-home-empty baseline
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    data_provenance: Mapped[str] = mapped_column(String(16), default="synthetic")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSession(Base):
    """Pre-V3 B-3: one row per sign-in (device). Access and refresh tokens carry its id (`sid`); every request checks
    it, so revoking a session cuts the device off at its next request, not when the access token expires."""

    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # random, = the tokens' sid claim
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    refresh_jti: Mapped[str] = mapped_column(String(32))  # the ONE refresh token currently valid for this session
    prev_refresh_jti: Mapped[str | None] = mapped_column(String(32))  # accepted for a short grace after rotation
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # None = system (refresh-token reuse)
    revoke_reason: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------- markets & data


class Mandi(Base):
    __tablename__ = "mandis"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)  # canonical name
    district: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    coords_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    geofence_radius_m: Mapped[float] = mapped_column(Float, default=500.0)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # raw Agmarknet spellings


class Price(Base):
    """Rs per quintal, as published by Agmarknet."""

    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("mandi_id", "commodity", "variety", "grade", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(50), index=True)
    variety: Mapped[str] = mapped_column(String(80), default="")
    grade: Mapped[str] = mapped_column(String(40), default="")
    date: Mapped[date] = mapped_column(Date, index=True)
    min_price: Mapped[float | None] = mapped_column(Float)
    max_price: Mapped[float | None] = mapped_column(Float)
    modal_price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="agmarknet")
    is_outlier: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Arrival(Base):
    """Tonnes arriving at a mandi per day (bulk Agmarknet downloads / trader-confirmed)."""

    __tablename__ = "arrivals"
    __table_args__ = (UniqueConstraint("mandi_id", "commodity", "date", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(50))
    date: Mapped[date] = mapped_column(Date, index=True)
    tonnes: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="agmarknet_bulk")


class Weather(Base):
    __tablename__ = "weather"
    __table_args__ = (UniqueConstraint("mandi_id", "date", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(20))  # open_meteo|nasa_power
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)
    precip_mm: Mapped[float | None] = mapped_column(Float)
    tmax_c: Mapped[float | None] = mapped_column(Float)
    tmin_c: Mapped[float | None] = mapped_column(Float)
    rh_pct: Mapped[float | None] = mapped_column(Float)
    solar_kwh_m2: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class WeatherForecast(Base):
    """Archive of weather forecasts AS ISSUED (V2-1). The weather table keeps only the latest forecast and
    overwrites it hourly; backtests need what the forecast said on each past day, so every run is kept here,
    keyed by the IST date it was issued on (the day's last fetch wins)."""

    __tablename__ = "weather_forecasts"
    __table_args__ = (UniqueConstraint("mandi_id", "issued_on", "target_date", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    issued_on: Mapped[date] = mapped_column(Date, index=True)
    target_date: Mapped[date] = mapped_column(Date)
    lead_days: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="open_meteo")
    precip_mm: Mapped[float | None] = mapped_column(Float)
    tmax_c: Mapped[float | None] = mapped_column(Float)
    tmin_c: Mapped[float | None] = mapped_column(Float)
    rh_pct: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class GraphEdge(Base):
    """Mandi graph edges (V2-3), one row per (build, src, dst, edge_type). Written by `python -m agripulse_ml.graph.build`.
    distance: REAL road km. price_corr: from price history (synthetic until real history exists).
    flow_estimate: an ESTIMATE (is_estimate = true), a relative index, never measured tonnes."""

    __tablename__ = "graph_edges"
    __table_args__ = (UniqueConstraint("build_id", "src_mandi_id", "dst_mandi_id", "edge_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    build_id: Mapped[str] = mapped_column(String(32), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)  # built from data published before this date
    src_mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    dst_mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    edge_type: Mapped[str] = mapped_column(String(20))  # distance | price_corr | flow_estimate
    weight: Mapped[float] = mapped_column(Float)
    km: Mapped[float | None] = mapped_column(Float)
    distance_source: Mapped[str | None] = mapped_column(String(20))  # osrm | haversine
    correlation: Mapped[float | None] = mapped_column(Float)
    flow_index: Mapped[float | None] = mapped_column(Float)
    data_provenance: Mapped[str] = mapped_column(String(20))
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SatelliteObs(Base):
    """Sentinel-2 NDVI over cropland, one row per (scene, district) (V2-4, REAL data).
    Written by `python -m agripulse_ml.satellite.load`. District areas are approximated as circles around the
    district HQ (config/satellite.toml). ndvi_* is NULL when the district was too cloudy in that scene."""

    __tablename__ = "satellite_obs"
    __table_args__ = (UniqueConstraint("scene_id", "district"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district: Mapped[str] = mapped_column(String(100), index=True)
    scene_id: Mapped[str] = mapped_column(String(80))
    date: Mapped[date] = mapped_column(Date, index=True)  # acquisition date (UTC)
    tile: Mapped[str | None] = mapped_column(String(20))
    baseline: Mapped[str | None] = mapped_column(String(10))
    scene_cloud_pct: Mapped[float | None] = mapped_column(Float)
    cropland_px: Mapped[int] = mapped_column(Integer)
    in_scene_px: Mapped[int] = mapped_column(Integer)
    clear_px: Mapped[int] = mapped_column(Integer)
    clear_frac: Mapped[float] = mapped_column(Float)
    ndvi_median: Mapped[float | None] = mapped_column(Float)
    ndvi_mean: Mapped[float | None] = mapped_column(Float)
    ndvi_p25: Mapped[float | None] = mapped_column(Float)
    ndvi_p75: Mapped[float | None] = mapped_column(Float)
    loaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Forecast(Base):
    """Always a range + spike probability, never a single point (rule 3)."""

    __tablename__ = "forecasts"
    __table_args__ = (UniqueConstraint("mandi_id", "commodity", "issue_date", "horizon_weeks", "model_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(50))
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    target_date: Mapped[date] = mapped_column(Date)
    horizon_weeks: Mapped[int] = mapped_column(Integer)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    spike_prob: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(40))
    model_version: Mapped[str] = mapped_column(String(40), default="")
    trained_on_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)  # V1 flag, kept for compatibility
    data_provenance: Mapped[str] = mapped_column(String(16), default="real", server_default="real")  # real|real_partial|synthetic
    # Pre-V3 B-1: p10 / p90 above are what users see. calibration = applied | not_yet_applicable | none;
    # p10_raw / p90_raw keep the model's own range (the calibrator learns from those, never from its own output).
    calibration: Mapped[str] = mapped_column(String(24), default="none", server_default="none")
    p10_raw: Mapped[float | None] = mapped_column(Float)
    p90_raw: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DataSourceRun(Base):
    __tablename__ = "data_source_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|failed
    rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- logistics


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)  # fleet
    registration: Mapped[str] = mapped_column(String(20), unique=True)
    capacity_tons: Mapped[float] = mapped_column(Float, default=5.0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Shipment(Base):
    """An FPO's grouping of farmer lots bound for one mandi."""

    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)  # FPO
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"))
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|booked|in_transit|delivered
    fleet_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)  # booked with
    booked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lots: Mapped[list["Lot"]] = relationship(back_populates="shipment", foreign_keys="Lot.shipment_id")
    mandi: Mapped[Mandi] = relationship()


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)  # FPO
    lender_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))  # farmer-granted
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    # the farmer's own choice from "Best mandi" (a request to the FPO; the shipment's mandi is what actually happens)
    preferred_mandi_id: Mapped[int | None] = mapped_column(ForeignKey("mandis.id"))
    transport_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # farmer asked the FPO to ship it
    # payment is RECORDED here (who paid, how, reference); no money moves through AgriPulse
    payment_method: Mapped[str | None] = mapped_column(String(20))  # upi|cash|bank|other (+ " (simulated)" in the demo)
    payment_ref: Mapped[str | None] = mapped_column(String(80))
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    payment_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # farmer confirmed receipt
    crop: Mapped[str] = mapped_column(String(50), default="Tomato")
    quantity_tons: Mapped[float] = mapped_column(Float)
    grade: Mapped[str] = mapped_column(String(20), default="Local")
    pickup_label: Mapped[str] = mapped_column(String(200), default="")
    pickup_lat: Mapped[float] = mapped_column(Float)
    pickup_lon: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="registered")
    # registered|grouped|in_transit|at_mandi|delivered
    delivered_weight_kg: Mapped[float | None] = mapped_column(Float)
    sale_price_per_quintal: Mapped[float | None] = mapped_column(Float)
    payout_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|paid
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    shipment: Mapped[Shipment | None] = relationship(back_populates="lots")
    farmer: Mapped[User] = relationship(foreign_keys=[farmer_id])


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    fleet_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"), index=True)
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    load_tons: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="assigned")
    # assigned|accepted|declined|in_progress|completed|cancelled
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_given_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pickup_qr_token: Mapped[str] = mapped_column(String(64), unique=True)
    delivery_qr_token: Mapped[str] = mapped_column(String(64), unique=True)
    pickup_scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivery_scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    share_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    planned_distance_km: Mapped[float | None] = mapped_column(Float)
    planned_duration_min: Mapped[float | None] = mapped_column(Float)
    route_geometry: Mapped[list | None] = mapped_column(JSON)  # [[lon, lat], ...]
    route_source: Mapped[str] = mapped_column(String(16), default="")  # osrm|haversine
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lon: Mapped[float | None] = mapped_column(Float)
    last_speed_kmph: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    remaining_km: Mapped[float | None] = mapped_column(Float)
    eta_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stopped_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Pre-V3 B-2: the driver's phone said location stopped (e.g. screen off in the browser app); cleared by the next fix
    tracking_paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    tracking_pause_reason: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    vehicle: Mapped[Vehicle] = relationship()
    mandi: Mapped[Mandi] = relationship()
    shipment: Mapped[Shipment | None] = relationship()


class GpsPoint(Base):
    """PK (trip_id, recorded_at) makes offline re-sync idempotent and satisfies
    TimescaleDB's rule that unique keys include the time column."""

    __tablename__ = "gps_points"
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    speed_kmph: Mapped[float | None] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class GeofenceEvent(Base):
    __tablename__ = "geofence_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    # picked_up|left_pickup_zone|reached_mandi|unexpected_stop|delivered
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # price_spike|vehicle_delay|vehicle_arrived|...
    severity: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    channels: Mapped[dict] = mapped_column(JSON, default=dict)  # {"in_app": "sent", "email": "skipped"}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditLog(Base):
    """Append-only record of every lifecycle transition (lot, shipment, trip, payout)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(16), index=True)  # lot|shipment|trip
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(24), default="status")  # status|payout_status
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # None = system (geofence, simulator)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelRun(Base):
    """One walk-forward evaluation (V2 harness). Metrics live in eval_results."""

    __tablename__ = "model_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    models: Mapped[list] = mapped_column(JSON, default=list)
    feature_set: Mapped[str] = mapped_column(String(80))
    data_provenance: Mapped[str] = mapped_column(String(16), index=True)
    fold_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    folds: Mapped[list] = mapped_column(JSON, default=list)
    data_start: Mapped[date | None] = mapped_column(Date)
    data_end: Mapped[date | None] = mapped_column(Date)
    n_mandis: Mapped[int] = mapped_column(Integer, default=0)
    alert_probability: Mapped[float] = mapped_column(Float, default=0.5)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(40), default="")  # e.g. v1_train, v2_baseline
    notes: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class EvalResult(Base):
    """Long-format metrics: one row per (run, model, feature_set, horizon, mandi, metric)."""

    __tablename__ = "eval_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("model_runs.run_id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(40))
    feature_set: Mapped[str] = mapped_column(String(80))
    horizon: Mapped[str] = mapped_column(String(12))  # "1".."4" weeks, or "spike_14d"
    mandi: Mapped[str] = mapped_column(String(16))  # mandi id, or "ALL"
    data_provenance: Mapped[str] = mapped_column(String(16))
    metric_name: Mapped[str] = mapped_column(String(40))
    metric_value: Mapped[float | None] = mapped_column(Float)


class TransportBooking(Base):
    """A farmer booking a transporter (fleet) directly for a pickup slot. The fleet owner confirms it by assigning a
    truck and driver (which creates the trip) or declines it."""

    __tablename__ = "transport_bookings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    fleet_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    mandi_id: Mapped[int] = mapped_column(ForeignKey("mandis.id"))
    pickup_at: Mapped[datetime] = mapped_column(UTCDateTime)
    fare_estimate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="requested")  # requested|confirmed|declined|cancelled
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"))
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
