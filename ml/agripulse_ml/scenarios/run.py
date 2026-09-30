"""Run a scenario: both channels over the stored display-model forecasts; store it in scenario_runs.
Never writes `forecasts` (tests/test_scenarios.py checks the table byte for byte)."""
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agripulse_api.modelcfg import display_model
from agripulse_api.models import Forecast, Mandi, ScenarioRun, User
from agripulse_api.provenance import worst

from . import SCENARIOS, assumptions as B, label, scenario_config
from .model import model_channel


def _date(v, name) -> date:
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    except ValueError:
        raise HTTPException(422, f"{name} must be a date (YYYY-MM-DD)")


def validate(db: Session, scenario: str, params: dict) -> dict:
    cfg = scenario_config()
    if scenario not in SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {list(SCENARIOS)}")
    if scenario == "rainfall_failure":
        districts = [str(x) for x in params.get("districts") or []]
        known = set(db.scalars(select(Mandi.district).where(Mandi.lat.is_not(None))))
        if not districts or not set(districts) <= known:
            raise HTTPException(422, f"districts must be a non-empty subset of {sorted(known)}")
        deficit = float(params.get("deficit", 0))
        if not 0 <= deficit <= 1:
            raise HTTPException(422, "deficit is a fraction of normal rainfall lost, 0..1")
        start, end = _date(params.get("start"), "start"), _date(params.get("end"), "end")
        if end < start or (end - start).days > 366:
            raise HTTPException(422, "window: start <= end, at most one year")
        return {"districts": sorted(districts), "deficit": deficit, "start": start, "end": end}
    start = _date(params.get("start"), "start")
    share = params.get("share")
    user_share = share is not None
    share = float(share) if user_share else B.export_share(cfg)
    if not 0 <= share <= cfg["export_ban"]["max_user_share"]:
        raise HTTPException(422, f"share must be 0..{cfg['export_ban']['max_user_share']}")
    return {"start": start, "share": share, "share_is_user_assumption": user_share}


def run(db: Session, user: User, scenario: str, raw: dict, model_dir: str | None = None) -> ScenarioRun:
    p = validate(db, scenario, raw)
    cfg = scenario_config()
    name = display_model()
    newest = db.scalar(select(func.max(Forecast.issue_date)).where(Forecast.model_name == name))
    if newest is None:
        raise HTTPException(409, "No display-model forecasts yet: nothing to shift. Run the forecast job first.")
    rows = db.scalars(select(Forecast).where(Forecast.model_name == name, Forecast.issue_date >= newest - timedelta(days=7))
                      .order_by(Forecast.issue_date)).all()
    latest = {}
    for f in rows:  # newest forecast per mandi x horizon (same rule as the forecast screens)
        latest[(f.mandi_id, f.horizon_weeks)] = f
    mandis = {m.id: m for m in db.scalars(select(Mandi).where(Mandi.id.in_({k[0] for k in latest})))}
    region = ({m.id for m in mandis.values() if m.district in p["districts"]} if scenario == "rainfall_failure"
              else set(mandis))
    if scenario == "rainfall_failure":
        mult = B.multipliers(B.rainfall_supply_changes(p["deficit"], cfg), cfg)
        win = B.affected_window(p["start"], p["end"], cfg)
        applies = lambda m, t: m in region and win[0] <= t <= win[1]  # noqa: E731
    else:
        mult = B.multipliers({"central": p["share"], "all": [p["share"]]}, cfg)
        win = (p["start"], None)
        applies = lambda m, t: t >= p["start"]  # noqa: E731
    A = model_channel(db, scenario, p, region, model_dir)
    results = []
    for mid, m in sorted(mandis.items(), key=lambda kv: kv[1].name):
        hs = []
        for h in (1, 2, 3, 4):
            f = latest.get((mid, h))
            if f is None:
                continue
            base = {"p10": f.p10, "p50": f.p50, "p90": f.p90}
            on = applies(mid, f.target_date)
            b = B.shift(f.p10, f.p50, f.p90, mult) if on else dict(base)
            ra = A["ratios"].get(mid, {}).get(h) if A["available"] else None
            a = {q: base[q] * ra[q] for q in ("p10", "p50", "p90")} if ra else None
            if a:  # keep the band ordered after applying per-quantile ratios
                a = {"p10": min(a.values()), "p50": sorted(a.values())[1], "p90": max(a.values())}
            hs.append({"weeks": h, "target_date": str(f.target_date), "baseline": {k: round(v) for k, v in base.items()},
                       "assumption": {**{k: round(v) for k, v in b.items()}, "applies": on},
                       "model": {k: round(v) for k, v in a.items()} if a else None,
                       "calibration": f.calibration, "data_provenance": f.data_provenance})
        results.append({"mandi_id": mid, "mandi": m.name, "district": m.district, "lat": m.lat, "lon": m.lon,
                        "in_region": mid in region, "horizons": hs, "spike": A["spike"].get(mid)})

    def pct(ch, h):
        vals = [(x[ch]["p50"] / x["baseline"]["p50"] - 1) * 100 for r in results if r["in_region"] for x in r["horizons"]
                if x["weeks"] == h and x[ch] and x["baseline"]["p50"]]
        return round(sum(vals) / len(vals), 2) if vals else None

    b_pct = {h: pct("assumption", h) for h in (1, 2, 3, 4)}
    a_pct = {h: pct("model", h) for h in (1, 2, 3, 4)} if A["available"] else {}
    # the two channels point in opposite directions (each moves more than 0.5%): say so, don't average it away
    disagree = [str(h) for h in (1, 2, 3, 4) if a_pct.get(h) is not None and b_pct[h] is not None
                and abs(a_pct[h]) > 0.5 and abs(b_pct[h]) > 0.5 and (a_pct[h] > 0) != (b_pct[h] > 0)]
    summary = {"affected_mandis": len(region), "multipliers": {k: round(v, 4) for k, v in mult.items()},
               "channels_disagree_weeks": disagree,
               "window_affects_arrivals": [str(win[0]), str(win[1]) if win[1] else None],
               "assumption_p50_shift_pct": {str(h): pct("assumption", h) for h in (1, 2, 3, 4)},
               "model_p50_shift_pct": {str(h): pct("model", h) for h in (1, 2, 3, 4)} if A["available"] else None,
               "model_available": A["available"], "model_reason": A.get("reason"), "model_notes": A["notes"]}
    used = {"demand": cfg["demand"], **({"rainfall_failure": cfg["rainfall_failure"]} if scenario == "rainfall_failure"
                                        else {"export_ban": {**cfg["export_ban"], "share_used": p["share"]}})}
    run = ScenarioRun(scenario=scenario, params={k: (str(v) if isinstance(v, date) else v) for k, v in p.items()},
                      created_by=user.id, model_name=name, forecast_issue_date=newest,
                      data_provenance=worst(*(f.data_provenance for f in latest.values())), label=label(),
                      assumptions=used, summary=summary, results=results)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_out(r: ScenarioRun, full: bool = True) -> dict:
    out = {"id": r.id, "label": r.label, "scenario": r.scenario, "params": r.params, "created_at": r.created_at,
           "model_name": r.model_name, "forecast_issue_date": r.forecast_issue_date, "data_provenance": r.data_provenance,
           "summary": r.summary}
    return {**out, "assumptions": r.assumptions, "results": r.results} if full else out
