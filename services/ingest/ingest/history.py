"""Real price HISTORY from data.gov.in's "Variety-wise Daily Market Prices Data of Commodity" resource.

Rule 5: nothing about this resource is trusted until `probe()` has looked at real responses. What a third-party client
(and nothing we stored) suggests: resource 35985678-0d79-46b4-9ed6-6f13308a1d24, capitalised keys (State, District,
Market, Commodity, Variety, Grade, Arrival_Date, Min_Price, Max_Price, Modal_Price) and filters[State] /
filters[Commodity] / filters[Arrival_Date]. The probe checks each of those, both key spellings, the date format, the
page cap and how far back the data goes, and stores what it saw (data_source_runs "agmarknet_history_probe" + the log).
`backfill()` only runs with a probe that found usable filters, and uses exactly what the probe found.

    python -m ingest.history probe
    python -m ingest.history backfill [--days 800]
"""
import argparse
import json
import logging
import os
import threading
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.models import DataSourceRun

from .agmarknet import HEADERS, AgmarknetError, note_feed_commodities, store_records
from .cleaning import parse_date
from .runs import tracked_run

log = logging.getLogger("agripulse.history")
SOURCE = "agmarknet_hist"  # prices.source for history rows (real; readiness counts every non-synthetic source)
PAGE = 1000


def _url() -> str:
    s = get_settings()
    return f"{s.agmarknet_base_url}/{s.agmarknet_history_resource_id}"


def _get(client: httpx.Client, params: dict, retries: int = 3) -> dict:
    s = get_settings()
    if not s.data_gov_api_key:
        raise AgmarknetError("DATA_GOV_API_KEY is not set")
    last = None
    for attempt in range(retries):
        try:
            r = client.get(_url(), params={"api-key": s.data_gov_api_key, "format": "json", **params},
                           headers=HEADERS, timeout=60)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "records" not in data:
                raise AgmarknetError(f"unexpected response keys {list(data)[:10] if isinstance(data, dict) else type(data)}")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise AgmarknetError(f"history request failed: {last}")


def lower_keys(rec: dict) -> dict:
    return {str(k).strip().lower(): v for k, v in rec.items()}


def _total(d: dict) -> int | None:
    try:
        return int(d.get("total"))
    except (TypeError, ValueError):
        return None


def _recent_weekday(days_back: int) -> date:
    d = date.today() - timedelta(days=days_back)
    while d.weekday() == 6:  # mandis close on Sundays
        d -= timedelta(days=1)
    return d


def probe(db: Session, client: httpx.Client | None = None) -> dict:
    """Look at the real resource and write down what it does. Returns the findings (also stored + logged)."""
    own = client is None
    client = client or httpx.Client()
    f: dict = {"resource": get_settings().agmarknet_history_resource_id}
    try:
        with tracked_run(db, "agmarknet_history_probe") as run:
            plain = _get(client, {"limit": 3})
            f["wrapper_keys"] = sorted(plain.keys())
            f["total_all"] = _total(plain)
            f["limit_echo"] = plain.get("limit")
            f["fields"] = plain.get("field")  # data.gov.in lists field ids/types here when present
            f["sample"] = plain.get("records", [])[:3]
            f["record_keys"] = sorted(plain["records"][0].keys()) if plain.get("records") else []
            # which filter key spelling narrows the result
            for spelling in ("State", "state", "state.keyword"):
                d = _get(client, {"limit": 1, f"filters[{spelling}]": "Karnataka"})
                t = _total(d)
                f.setdefault("state_filter_totals", {})[spelling] = t
                if t and (f["total_all"] is None or t < f["total_all"]) and d.get("records") \
                        and str(lower_keys(d["records"][0]).get("state", "")).lower() == "karnataka":
                    f.setdefault("state_key", spelling)
            ck = None
            for spelling in ("Commodity", "commodity"):
                params = {"limit": 1, f"filters[{spelling}]": "Tomato"}
                if f.get("state_key"):
                    params[f"filters[{f['state_key']}]"] = "Karnataka"
                d = _get(client, params)
                t = _total(d)
                f.setdefault("commodity_filter_totals", {})[spelling] = t
                if t and d.get("records") and str(lower_keys(d["records"][0]).get("commodity", "")).lower() == "tomato":
                    ck = ck or spelling
            f["commodity_key"] = ck
            if ck and f.get("state_key"):
                f["total_karnataka_tomato"] = f["commodity_filter_totals"][ck]
            # date filter: key spelling x format
            day = _recent_weekday(30)
            for spelling in ("Arrival_Date", "arrival_date"):
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                    v = day.strftime(fmt)
                    params = {"limit": 5, f"filters[{spelling}]": v}
                    if f.get("state_key"):
                        params[f"filters[{f['state_key']}]"] = "Karnataka"
                    d = _get(client, params)
                    t = _total(d)
                    ok = bool(d.get("records")) and all(
                        _same_day(lower_keys(r).get("arrival_date"), day) for r in d["records"])
                    f.setdefault("date_filter_tries", []).append({"key": spelling, "value": v, "total": t, "matches": ok})
                    if ok and "date_key" not in f:
                        f["date_key"], f["date_format"], f["total_karnataka_one_day"] = spelling, fmt, t
            # how far back: ask for the oldest record, if sorting works
            if f.get("state_key") and ck:
                for sk in ("Arrival_Date", "arrival_date"):
                    try:
                        d = _get(client, {"limit": 1, f"filters[{f['state_key']}]": "Karnataka",
                                          f"filters[{ck}]": "Tomato", f"sort[{sk}]": "asc"})
                    except AgmarknetError as exc:
                        f.setdefault("sort_tries", []).append({"key": sk, "error": str(exc)[:200]})
                        continue
                    first = lower_keys(d["records"][0]).get("arrival_date") if d.get("records") else None
                    f.setdefault("sort_tries", []).append({"key": sk, "first_arrival_date": first})
                # an old day, to see whether history is actually served
                for back in (400, 800):
                    if f.get("date_key"):
                        old = _recent_weekday(back)
                        d = _get(client, {"limit": 1, f"filters[{f['state_key']}]": "Karnataka", f"filters[{ck}]": "Tomato",
                                          f"filters[{f['date_key']}]": old.strftime(f["date_format"])})
                        f.setdefault("old_day_totals", {})[old.isoformat()] = _total(d)
            # page cap: ask for more than PAGE and see how many come back
            if f.get("state_key"):
                d = _get(client, {"limit": 2000, f"filters[{f['state_key']}]": "Karnataka"})
                f["page_cap_seen"] = len(d.get("records") or [])
            f["usable"] = bool(f.get("state_key") and ck)
            run.rows, run.details = len(f.get("sample", [])), f
    finally:
        if own:
            client.close()
    log.info("history probe findings: %s", json.dumps(f, default=str)[:6000])
    return f


def _same_day(v, day: date) -> bool:
    try:
        return parse_date(v) == day
    except (ValueError, TypeError):
        return False


def last_probe(db: Session) -> dict | None:
    run = db.scalar(select(DataSourceRun).where(DataSourceRun.source == "agmarknet_history_probe",
                                                DataSourceRun.status == "success").order_by(DataSourceRun.id.desc()))
    return run.details if run else None


def _done_pairs(db: Session, since_days: int) -> set[tuple[str, str]]:
    runs = db.scalars(select(DataSourceRun).where(DataSourceRun.source == "agmarknet_history",
                                                  DataSourceRun.status == "success"))
    return {(r.details.get("state"), r.details.get("commodity")) for r in runs
            if r.details and r.details.get("days", 0) >= since_days}


def backfill_pair(db: Session, client: httpx.Client, f: dict, state: str, commodity_raw: str, since: date,
                  max_rows: int = 400_000) -> dict:
    """Page through one (state, commodity) history and store rows on/after `since`. Offsets only: the probe decides
    whether the filters work; nothing about ordering is assumed (we keep paging until `total`)."""
    with tracked_run(db, "agmarknet_history") as run:
        counts = {"state": state, "commodity": commodity_raw, "days": (date.today() - since).days,
                  "fetched": 0, "stored": 0, "updated": 0, "outliers": 0, "older_skipped": 0, "total": None}
        offset = 0
        while offset < max_rows:
            d = _get(client, {"limit": PAGE, "offset": offset, f"filters[{f['state_key']}]": state,
                              f"filters[{f['commodity_key']}]": commodity_raw})
            recs = [lower_keys(r) for r in d.get("records") or []]
            counts["total"] = _total(d)
            if not recs:
                break
            offset += len(recs)
            counts["fetched"] += len(recs)
            keep = []
            for r in recs:
                try:
                    if parse_date(r.get("arrival_date")) >= since:
                        keep.append(r)
                    else:
                        counts["older_skipped"] += 1
                except (ValueError, TypeError):
                    continue
            note_feed_commodities(db, keep[:1])
            c = store_records(db, keep, source=SOURCE)
            for k in ("stored", "updated", "outliers"):
                counts[k] += c[k]
            db.commit()
            if counts["total"] is not None and offset >= counts["total"]:
                break
        run.rows = counts["stored"] + counts["updated"]
        run.details = counts
        return counts


def backfill(db: Session, days: int | None = None, veg_days: int | None = None, client: httpx.Client | None = None) -> dict:
    """Tomato (the forecast crop) back `days`; every other tracked vegetable back `veg_days`. Resumable: a
    (state, commodity) pair already done to at least that depth is skipped."""
    from agripulse_api.crops import crops, has_forecast

    s = get_settings()
    days = days or int(os.environ.get("HISTORY_DAYS", "800"))
    veg_days = veg_days or int(os.environ.get("HISTORY_VEG_DAYS", "120"))
    f = last_probe(db)
    if not f or not f.get("usable"):
        return {"skipped": "no usable probe (run: python -m ingest.history probe)", "probe": f}
    own = client is None
    client = client or httpx.Client()
    out = {"pairs": []}
    try:
        for c in crops():
            feed = c.get("agmarknet") or c["name"]
            depth = days if has_forecast(c["name"]) else veg_days
            done = _done_pairs(db, depth)
            for state in s.state_list:
                if (state, feed) in done:
                    continue
                try:
                    out["pairs"].append(backfill_pair(db, client, f, state, feed, date.today() - timedelta(days=depth)))
                except AgmarknetError as exc:
                    out["pairs"].append({"state": state, "commodity": feed, "error": str(exc)[:300]})
                    db.rollback()
    finally:
        if own:
            client.close()
    return out


class KeepAwake:
    """Free hosts stop an instance with no inbound traffic; long backfills ping our own public URL meanwhile."""

    def __init__(self, every_s: int = 240):
        self.url = os.environ.get("RENDER_EXTERNAL_URL")
        self.every = every_s
        self._stop = threading.Event()

    def __enter__(self):
        if self.url:
            threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                httpx.get(f"{self.url}/health", timeout=20)
            except httpx.HTTPError:
                pass

    def __exit__(self, *exc):
        self._stop.set()


def main() -> None:
    from agripulse_api.db import SessionLocal

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["probe", "backfill"])
    ap.add_argument("--days", type=int)
    ap.add_argument("--veg-days", type=int)
    a = ap.parse_args()
    with SessionLocal() as db:
        out = probe(db) if a.job == "probe" else backfill(db, a.days, a.veg_days)
    print(json.dumps(out, indent=2, default=str)[:20000])


if __name__ == "__main__":
    main()
