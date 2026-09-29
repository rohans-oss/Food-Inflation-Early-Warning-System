"""Synthetic vehicle simulator (rule 1: everything it makes is is_simulated=True).

Generates trucks driving real routes (OSRM when configured, straight line otherwise)
from farm areas to seeded mandis, and feeds their positions through the SAME
ingestion path real phones use, so geofences, ETA, in-transit supply and alerts
are exercised end to end.

Start it from Admin > Simulator (runs inside the API process), or from a shell:
    python -m tracking.simulator --trips 8 --speedup 1
The shell variant needs REDIS_URL for live map updates to reach the API process.
"""
import argparse
import asyncio
import logging
import math
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api import db as dbmod
from agripulse_api.lifecycle import move
from agripulse_api.models import Mandi, Organization, Trip, Vehicle
from agripulse_api.security import new_token

from . import routing
from .engine import process_points
from .geo import haversine_km

log = logging.getLogger("agripulse.simulator")
SIM_ORG = "Simulated Fleet (synthetic)"


def _sim_org(db: Session) -> Organization:
    org = db.scalar(select(Organization).where(Organization.name == SIM_ORG))
    if org is None:
        org = Organization(name=SIM_ORG, kind="fleet")
        db.add(org)
        db.flush()
    return org


def create_trips(db: Session, n: int, seed: int | None = None, now: datetime | None = None) -> list[int]:
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    org = _sim_org(db)
    mandis = [m for m in db.scalars(select(Mandi).where(Mandi.lat.is_not(None)))]
    if not mandis:
        raise RuntimeError("No mandis with coordinates")
    ids = []
    for _ in range(n):
        m = rng.choice(mandis)
        dist_km, bearing = rng.uniform(25, 90), rng.uniform(0, 2 * math.pi)
        o_lat = m.lat + dist_km / 111.0 * math.cos(bearing)
        o_lon = m.lon + dist_km / (111.0 * math.cos(math.radians(m.lat))) * math.sin(bearing)
        reg = f"SIM-KA-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        v = db.scalar(select(Vehicle).where(Vehicle.registration == reg)) or Vehicle(
            org_id=org.id, registration=reg, capacity_tons=rng.choice([3.0, 5.0, 7.5, 10.0]), is_simulated=True)
        db.add(v)
        db.flush()
        r = routing.route(o_lat, o_lon, m.lat, m.lon)
        t = Trip(vehicle_id=v.id, fleet_org_id=org.id, mandi_id=m.id, origin_lat=o_lat, origin_lon=o_lon,
                 load_tons=round(rng.uniform(0.5, 1.0) * v.capacity_tons, 1), status="in_progress", is_simulated=True,
                 consent_given_at=now, started_at=now, pickup_scanned_at=now,
                 pickup_qr_token=new_token(), delivery_qr_token=new_token(),
                 planned_distance_km=r.distance_km, planned_duration_min=r.duration_min, route_geometry=r.geometry,
                 route_source=r.source, remaining_km=r.distance_km)
        db.add(t)
        db.flush()
        ids.append(t.id)
    db.commit()
    return ids


def _point_along(route: list[list[float]], km: float) -> tuple[float, float, bool]:
    """(lat, lon, finished) after travelling `km` along the polyline."""
    left = km
    for a, b in zip(route, route[1:]):
        seg = haversine_km(a[1], a[0], b[1], b[0])
        if left <= seg:
            f = left / seg if seg else 0
            return a[1] + (b[1] - a[1]) * f, a[0] + (b[0] - a[0]) * f, False
        left -= seg
    return route[-1][1], route[-1][0], True


class SimState:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.trip_ids: list[int] = []
        self.progress: dict[int, float] = {}  # km travelled
        self.stops: dict[int, int] = {}  # ticks left in a stop
        self.speedup = 1.0
        self.started_at: datetime | None = None

    def status(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "trips": len(self.trip_ids),
                "speedup": self.speedup, "started_at": self.started_at}


state = SimState()


def tick(db: Session, st: SimState, dt_s: float, rng: random.Random, now: datetime | None = None) -> int:
    """Advance every simulated trip by dt_s * speedup seconds of driving. Returns trips still moving."""
    now = now or datetime.now(timezone.utc)
    moving = 0
    for tid in list(st.trip_ids):
        t = db.get(Trip, tid)
        if t is None or t.status != "in_progress":
            continue
        avg_kmph = t.planned_distance_km / max(t.planned_duration_min / 60, 0.1)
        if st.stops.get(tid, 0) > 0:
            st.stops[tid] -= 1
            speed = 0.0
        else:
            if rng.random() < 0.002:  # occasional long halt -> exercises unexpected_stop
                st.stops[tid] = int(40 * 60 / max(dt_s * st.speedup, 1))
            speed = max(5.0, rng.gauss(avg_kmph, avg_kmph * 0.15))
            st.progress[tid] = st.progress.get(tid, 0.0) + speed * dt_s * st.speedup / 3600
        lat, lon, done = _point_along(t.route_geometry, st.progress.get(tid, 0.0))
        process_points(db, t, [{"recorded_at": now, "lat": lat, "lon": lon, "speed_kmph": speed, "accuracy_m": 15}], now=now)
        if done:
            move(db, t, "completed", None, via="simulator")
            t.ended_at, t.delivery_scanned_at = now, now
        else:
            moving += 1
    db.commit()
    return moving


async def run(n_trips: int, speedup: float = 1.0, tick_s: float = 5.0, seed: int | None = None):
    rng = random.Random(seed)
    state.speedup, state.started_at, state.progress, state.stops = speedup, datetime.now(timezone.utc), {}, {}

    def setup():
        with dbmod.SessionLocal() as db:
            return create_trips(db, n_trips, seed)

    state.trip_ids = await asyncio.to_thread(setup)
    log.info("simulating %d trips at x%.1f", n_trips, speedup)
    while True:
        def step():
            with dbmod.SessionLocal() as db:
                return tick(db, state, tick_s, rng)

        if await asyncio.to_thread(step) == 0:
            break
        await asyncio.sleep(tick_s)
    log.info("simulation finished")


def start(n_trips: int, speedup: float) -> dict:
    if state.task and not state.task.done():
        raise RuntimeError("Simulator already running")
    state.task = asyncio.get_running_loop().create_task(run(n_trips, speedup))
    return state.status()


def stop(db: Session) -> dict:
    if state.task:
        state.task.cancel()
    for t in db.scalars(select(Trip).where(Trip.is_simulated.is_(True), Trip.status == "in_progress")):
        move(db, t, "cancelled", None, via="simulator_stop")
        t.ended_at = datetime.now(timezone.utc)
    db.commit()
    state.trip_ids = []
    return state.status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", type=int, default=8)
    ap.add_argument("--speedup", type=float, default=1.0)
    ap.add_argument("--tick", type=float, default=5.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    async def go():
        from .hub import hub

        await hub.start()  # with REDIS_URL, updates reach the API's websocket clients
        await run(args.trips, args.speedup, args.tick)

    asyncio.run(go())


if __name__ == "__main__":
    main()
