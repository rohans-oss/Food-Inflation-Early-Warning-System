import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from .. import readiness
from ..models import AuditLog, DataSourceRun, EvalResult, Mandi, ModelRun, Organization, Price, User
from ..provenance import LABEL
from ..rbac import ROLES, require
from .auth import user_out

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require("admin:all")

# How stale each source may get before the freshness monitor turns red.
EXPECTED_EVERY = {
    "agmarknet": timedelta(hours=26),
    "open_meteo": timedelta(hours=2),
    "nasa_power": timedelta(hours=26),
    "forecast": timedelta(hours=26),
    "graph_build": timedelta(days=8),  # V2-3, weekly
}


@router.get("/v2-results")
def v2_results_list(db: Session = Depends(get_db), _=Depends(admin_only)):
    """V2 findings with their data provenance (agripulse_api/v2_results.py), plus the live calibration status."""
    from ..v2_results import v2_results

    return v2_results(db)


@router.get("/freshness")
def freshness(db: Session = Depends(get_db), _=Depends(admin_only)):
    now = datetime.now(timezone.utc)
    out = []
    for source, budget in EXPECTED_EVERY.items():
        last_ok = db.scalar(
            select(DataSourceRun)
            .where(DataSourceRun.source == source, DataSourceRun.status == "success")
            .order_by(DataSourceRun.finished_at.desc())
            .limit(1)
        )
        last_any = db.scalar(
            select(DataSourceRun).where(DataSourceRun.source == source).order_by(DataSourceRun.started_at.desc()).limit(1)
        )
        age = (now - last_ok.finished_at) if last_ok and last_ok.finished_at else None
        out.append(
            {
                "source": source,
                "last_success_at": last_ok.finished_at if last_ok else None,
                "last_success_rows": last_ok.rows if last_ok else None,
                "last_run_status": last_any.status if last_any else None,
                "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
                "status": "never" if age is None else ("fresh" if age <= budget else "stale"),
                "budget_hours": budget.total_seconds() / 3600,
            }
        )
    latest_price = db.scalar(select(func.max(Price.date)))
    return {"sources": out, "latest_price_date": latest_price}


@router.get("/runs")
def runs(status: str | None = None, limit: int = 50, db: Session = Depends(get_db), _=Depends(admin_only)):
    q = select(DataSourceRun).order_by(DataSourceRun.started_at.desc()).limit(min(limit, 500))
    if status:
        q = q.where(DataSourceRun.status == status)
    return [
        {
            "id": r.id,
            "source": r.source,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "rows": r.rows,
            "error": r.error,
            "details": r.details,
        }
        for r in db.scalars(q)
    ]


@router.get("/data-quality")
def data_quality(db: Session = Depends(get_db), _=Depends(admin_only)):
    """Per-mandi coverage for the last 90 days: missing-day %, outlier count."""
    since = datetime.now(timezone.utc).date() - timedelta(days=90)
    rows = db.execute(
        select(
            Mandi.id,
            Mandi.name,
            Mandi.coords_verified,
            func.count(func.distinct(Price.date)),
            func.sum(case((Price.is_outlier, 1), else_=0)),
            func.max(Price.date),
        )
        .join(Price, (Price.mandi_id == Mandi.id) & (Price.date >= since), isouter=True)
        .group_by(Mandi.id, Mandi.name, Mandi.coords_verified)
        .order_by(Mandi.name)
    ).all()
    out = []
    for mid, name, verified, days, outliers, last in rows:
        out.append(
            {
                "mandi_id": mid,
                "mandi": name,
                "coords_verified": verified,
                "days_with_price": days,
                "missing_day_pct": round(100 * (1 - days / 90), 1),
                "outliers": int(outliers or 0),
                "last_price_date": last,
            }
        )
    return out


@router.get("/model-performance")
def model_performance(_=Depends(admin_only)):
    path = Path(get_settings().model_dir) / "backtest.json"
    if not path.exists():
        return {"available": False, "hint": "Run `python -m agripulse_ml.train` to produce a backtest."}
    rep = json.loads(path.read_text())
    # V1 backtest files predate data_provenance: derive it so nothing is shown unlabelled
    rep.setdefault("data_provenance", "synthetic" if rep.get("trained_on_synthetic") else "real")
    rep.setdefault("provenance_label", LABEL[rep["data_provenance"]])
    return {"available": True, **rep}


@router.get("/data-readiness")
def data_readiness(db: Session = Depends(get_db), _=Depends(admin_only)):
    """How much REAL history each mandi has per data type, vs config/readiness.toml."""
    return readiness.compute(db)


@router.get("/eval-runs")
def eval_runs(limit: int = 20, db: Session = Depends(get_db), _=Depends(admin_only)):
    """Recent walk-forward runs from the shared harness, with pooled (ALL-mandi) metrics."""
    runs = db.scalars(select(ModelRun).order_by(ModelRun.started_at.desc()).limit(min(limit, 100))).all()
    out = []
    for r in runs:
        rows = db.scalars(select(EvalResult).where(EvalResult.run_id == r.run_id, EvalResult.mandi == "ALL")).all()
        out.append({"run_id": r.run_id, "purpose": r.purpose, "models": r.models, "feature_set": r.feature_set,
                    "data_provenance": r.data_provenance, "provenance_label": LABEL[r.data_provenance],
                    "data_start": r.data_start, "data_end": r.data_end, "n_mandis": r.n_mandis,
                    "folds": len(r.folds or []), "started_at": r.started_at, "mlflow_run_id": r.mlflow_run_id,
                    "pooled": [{"model_name": x.model_name, "horizon": x.horizon, "metric_name": x.metric_name,
                                "metric_value": x.metric_value} for x in rows]})
    return out


# ---------------------------------------------------------------- users & orgs


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(admin_only)):
    from ..sessions import active_counts

    n = active_counts(db)
    return [{**user_out(u).model_dump(), "active_sessions": n.get(u.id, 0)} for u in db.scalars(select(User).order_by(User.id))]


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: int, body: RevokeIn | None = None, db: Session = Depends(get_db), admin=Depends(admin_only)):
    """Pre-V3 B-3: sign this user out everywhere, now. Their next request fails; they can sign in again unless the
    account is also disabled. Audited (who, when, why, how many)."""
    from ..sessions import revoke

    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    n = revoke(db, u.id, actor_id=admin.id, reason=body.reason if body else None, via="admin")
    db.commit()
    return {"user_id": u.id, "sessions_revoked": n}


@router.get("/users/{user_id}/session-audit")
def session_audit(user_id: int, db: Session = Depends(get_db), _=Depends(admin_only)):
    """Revocation history for one user (newest first)."""
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == user_id,
                                             AuditLog.field == "sessions").order_by(AuditLog.at.desc()).limit(50))
    return [{"at": r.at, "actor_id": r.actor_id, **(r.details or {})} for r in rows]


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = None
    org_id: int | None = None
    mandi_id: int | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), admin=Depends(admin_only)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, "Unknown role")
        u.role = body.role
    if body.is_active is not None:
        if u.id == admin.id and not body.is_active:
            raise HTTPException(400, "You cannot disable yourself")
        u.is_active = body.is_active
    if body.org_id is not None:
        u.org_id = body.org_id
    if body.mandi_id is not None:
        u.mandi_id = body.mandi_id
    db.commit()
    return user_out(u)


@router.get("/orgs")
def list_orgs(db: Session = Depends(get_db), _=Depends(admin_only)):
    return [{"id": o.id, "name": o.name, "kind": o.kind} for o in db.scalars(select(Organization).order_by(Organization.id))]


class OrgIn(BaseModel):
    name: str
    kind: str


@router.post("/orgs", status_code=201)
def create_org(body: OrgIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    if body.kind not in {"fpo", "fleet", "trader", "buyer", "lender", "government", "platform"}:
        raise HTTPException(400, "Unknown org kind")
    o = Organization(name=body.name, kind=body.kind)
    db.add(o)
    db.commit()
    return {"id": o.id, "name": o.name, "kind": o.kind}


class MandiPatch(BaseModel):
    lat: float | None = None
    lon: float | None = None
    coords_verified: bool | None = None
    geofence_radius_m: float | None = None
    merge_into_id: int | None = None  # fold a mis-spelt auto-created mandi into the canonical one


@router.patch("/mandis/{mandi_id}")
def patch_mandi(mandi_id: int, body: MandiPatch, db: Session = Depends(get_db), _=Depends(admin_only)):
    m = db.get(Mandi, mandi_id)
    if m is None:
        raise HTTPException(404, "Mandi not found")
    if body.merge_into_id:
        target = db.get(Mandi, body.merge_into_id)
        if target is None or target.id == m.id:
            raise HTTPException(400, "Bad merge target")
        from ..models import Arrival, Forecast, Weather

        # Re-point history to the canonical mandi. Where both already hold a row for
        # the same key, the canonical mandi's row wins and the duplicate is dropped.
        keys = {
            Price: ("commodity", "variety", "grade", "date"),
            Arrival: ("commodity", "date", "source"),
            Weather: ("date", "source"),
            Forecast: ("commodity", "issue_date", "horizon_weeks", "model_name"),
        }
        for model, cols in keys.items():
            existing = {
                tuple(getattr(r, c) for c in cols) for r in db.scalars(select(model).where(model.mandi_id == target.id))
            }
            for row in db.scalars(select(model).where(model.mandi_id == m.id)).all():
                if tuple(getattr(row, c) for c in cols) in existing:
                    db.delete(row)
                else:
                    row.mandi_id = target.id
        db.flush()
        target.aliases = sorted(set((target.aliases or []) + (m.aliases or []) + [m.name]))
        db.delete(m)
        db.commit()
        return {"merged_into": target.id}
    for field in ("lat", "lon", "coords_verified", "geofence_radius_m"):
        v = getattr(body, field)
        if v is not None:
            setattr(m, field, v)
    db.commit()
    return {"id": m.id, "lat": m.lat, "lon": m.lon, "coords_verified": m.coords_verified}


# ---------------------------------------------------------------- simulator (synthetic trips)


class SimIn(BaseModel):
    trips: int = 8
    speedup: float = 1.0


@router.post("/simulator/start")
async def sim_start(body: SimIn, _=Depends(admin_only)):
    from tracking import simulator

    if not 1 <= body.trips <= 100 or not 0.5 <= body.speedup <= 120:
        raise HTTPException(400, "trips 1-100, speedup 0.5-120")
    try:
        return simulator.start(body.trips, body.speedup)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/simulator/stop")
def sim_stop(db: Session = Depends(get_db), _=Depends(admin_only)):
    from tracking import simulator

    return simulator.stop(db)


@router.get("/simulator")
def sim_status(_=Depends(admin_only)):
    from tracking import simulator

    return simulator.state.status()


# ---------------------------------------------------------------- V3-0 recommenders


@router.get("/recommenders")
def recommenders(_=Depends(admin_only)):
    """Which recommender users get ([recommender] default in config/recommender.toml) and the study's result."""
    from ..decisions.service import RECOMMENDERS, default_recommender

    return {"default": default_recommender(), "available": list(RECOMMENDERS), "config": "config/recommender.toml",
            "study": "docs/optimizer-results.md"}


class CompareIn(BaseModel):
    density: str = "medium"
    seed: int = Field(default=1, ge=0, le=10_000)
    weeks: int = Field(default=1, ge=1, le=4)


@router.post("/recommenders/compare")
def compare_recommenders(body: CompareIn, db: Session = Depends(get_db), _=Depends(admin_only)):
    """Run the OLD rule and the optimizer on the same simulated batch of lots and trucks (V3-0, rule 21)."""
    from ..decisions.service import compare

    try:
        out = compare(db, body.density, body.seed, body.weeks)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if "error" in out:
        raise HTTPException(409, out["error"])
    return out
