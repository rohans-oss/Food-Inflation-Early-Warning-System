"""Agmarknet (data.gov.in) daily price ingestion + bulk history import.

Live API facts (checked against real responses; see docs/data-sources.md):
  GET https://api.data.gov.in/resource/<resource_id>
      ?api-key=..&format=json&limit=..&offset=..&filters[state]=..&filters[commodity]=..
  -> {"records": [...], "total": N, ...}
  record keys: state, district, market, commodity, variety, grade, arrival_date (DD/MM/YYYY),
               min_price, max_price, modal_price  (Rs per quintal)
  The endpoint serves the *current* day only and caps `limit`, so we page until `total`
  and store every day ourselves to build history. It carries no arrival tonnage.
"""
import csv
import io
import json
import time
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.config import get_settings
from agripulse_api.models import Arrival, Price

from .cleaning import MandiResolver, clean_record, history_jump_flag, parse_date, parse_num
from .runs import tracked_run

PAGE_SIZE = 1000
MAX_PAGES = 20
# Some gov-hosted endpoints stall requests without a browser-like UA.
HEADERS = {"User-Agent": "Mozilla/5.0 (AgriPulse data ingest; +https://github.com/rohans-oss)"}


class AgmarknetError(RuntimeError):
    pass


def fetch_state(client: httpx.Client, state: str, commodity: str) -> list[dict]:
    s = get_settings()
    if not s.data_gov_api_key:
        raise AgmarknetError("DATA_GOV_API_KEY is not set (get a free key at data.gov.in)")
    url = f"{s.agmarknet_base_url}/{s.agmarknet_resource_id}"
    records: list[dict] = []
    total = None
    for _ in range(MAX_PAGES):
        params = {
            "api-key": s.data_gov_api_key,
            "format": "json",
            "limit": PAGE_SIZE,
            "offset": len(records),
            "filters[state]": state,
            "filters[commodity]": commodity,
        }
        payload = _get_with_retry(client, url, params)
        rows = payload.get("records") or []
        total = int(payload.get("total") or len(rows))
        records.extend(rows)
        if not rows or len(records) >= total:
            break
    return records


def _get_with_retry(client: httpx.Client, url: str, params: dict, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = client.get(url, params=params, headers=HEADERS, timeout=45)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "records" not in data:
                raise AgmarknetError(f"Unexpected response shape: keys={list(data)[:10]}")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise AgmarknetError(f"data.gov.in request failed after {retries} tries: {last}")


def store_records(db: Session, records: list[dict], source: str = "agmarknet") -> dict:
    """Clean + upsert. Returns counts for the run log."""
    resolver = MandiResolver(db)
    counts = {"received": len(records), "stored": 0, "skipped_unusable": 0, "outliers": 0, "updated": 0}
    for raw in records:
        row = clean_record(raw)
        if row is None:
            counts["skipped_unusable"] += 1
            continue
        mandi = resolver.resolve(row.market, row.district, row.state)
        hist = db.scalars(
            select(Price.modal_price).where(
                Price.mandi_id == mandi.id,
                Price.commodity == row.commodity,
                Price.date < row.date,
                Price.date >= row.date - timedelta(days=30),
                Price.is_outlier.is_(False),
            )
        ).all()
        if history_jump_flag(row.modal_price, list(hist)):
            row.flags.append("jump_vs_history")
        existing = db.scalar(
            select(Price).where(
                Price.mandi_id == mandi.id,
                Price.commodity == row.commodity,
                Price.variety == row.variety,
                Price.grade == row.grade,
                Price.date == row.date,
            )
        )
        target = existing or Price(
            mandi_id=mandi.id, commodity=row.commodity, variety=row.variety, grade=row.grade, date=row.date
        )
        target.min_price, target.max_price, target.modal_price = row.min_price, row.max_price, row.modal_price
        target.is_outlier, target.quality_flags, target.source = row.is_outlier, row.flags, source
        if existing:
            counts["updated"] += 1
        else:
            db.add(target)
            counts["stored"] += 1
        counts["outliers"] += int(row.is_outlier)
        tonnes = parse_num(raw.get("arrivals_tonnes"))
        if tonnes is not None:
            _upsert_arrival(db, mandi.id, row.commodity, row.date, tonnes, source)
        db.flush()
    counts["new_mandis"] = resolver.created
    return counts


def _upsert_arrival(db: Session, mandi_id: int, commodity: str, day, tonnes: float, source: str) -> None:
    a = db.scalar(
        select(Arrival).where(
            Arrival.mandi_id == mandi_id, Arrival.commodity == commodity, Arrival.date == day, Arrival.source == source
        )
    )
    if a is None:
        db.add(Arrival(mandi_id=mandi_id, commodity=commodity, date=day, tonnes=tonnes, source=source))
    else:
        a.tonnes = tonnes


def run_daily(db: Session, client: httpx.Client | None = None, raw_dir: str | None = "data/raw/agmarknet") -> dict:
    """Scheduled job: pull today's tomato prices for every configured state."""
    s = get_settings()
    own = client is None
    client = client or httpx.Client()
    try:
        with tracked_run(db, "agmarknet") as run:
            per_state, all_records, failures = {}, [], {}
            for state in s.state_list:
                try:
                    recs = fetch_state(client, state, s.agmarknet_commodity)
                except AgmarknetError as exc:
                    failures[state] = str(exc)
                    continue
                per_state[state] = len(recs)
                all_records.extend(recs)
            if failures and not all_records:
                raise AgmarknetError("; ".join(f"{k}: {v}" for k, v in failures.items()))
            if raw_dir and all_records:
                _archive_raw(raw_dir, all_records)
            counts = store_records(db, all_records)
            run.rows = counts["stored"] + counts["updated"]
            run.details = {**counts, "per_state": per_state, "state_failures": failures}
            return run.details
    finally:
        if own:
            client.close()


def _archive_raw(raw_dir: str, records: list[dict]) -> None:
    """Keep the untouched payload per day: our own history is the only history the API gives."""
    day = records[0].get("arrival_date", "unknown").replace("/", "-")
    p = Path(raw_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{day}.json").write_text(json.dumps(records, ensure_ascii=False))


# ------------------------------------------------------------------ bulk history import

# Header spellings seen across Agmarknet report downloads / archived dumps. Unknown
# headers fail loudly instead of guessing (rule 5: inspect real files, don't invent).
CSV_ALIASES = {
    "state": ["state", "state name"],
    "district": ["district", "district name"],
    "market": ["market", "market name"],
    "commodity": ["commodity"],
    "variety": ["variety"],
    "grade": ["grade"],
    "arrival_date": ["arrival_date", "arrival date", "price date", "reported date", "date"],
    "min_price": ["min_price", "min price", "min price (rs./quintal)", "min_x0020_price"],
    "max_price": ["max_price", "max price", "max price (rs./quintal)", "max_x0020_price"],
    "modal_price": ["modal_price", "modal price", "modal price (rs./quintal)", "modal_x0020_price"],
    "arrivals_tonnes": ["arrivals (tonnes)", "arrivals_tonnes", "arrivals"],
}
REQUIRED = {"market", "commodity", "arrival_date", "modal_price"}


def map_headers(headers: list[str]) -> dict[str, str]:
    lower = {h.strip().lower(): h for h in headers}
    mapping = {}
    for field_name, options in CSV_ALIASES.items():
        for o in options:
            if o in lower:
                mapping[field_name] = lower[o]
                break
    missing = REQUIRED - set(mapping)
    if missing:
        raise AgmarknetError(f"CSV is missing columns {sorted(missing)}; headers were {headers}")
    return mapping


def import_file(db: Session, path: str, default_state: str = "", commodity_filter: str | None = "Tomato") -> dict:
    """Backfill from a CSV report download or a saved JSON payload ({"records": [...]} or [...])."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text())
        records = data["records"] if isinstance(data, dict) else data
    else:
        text = p.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        mapping = map_headers(reader.fieldnames or [])
        records = []
        for r in reader:
            rec = {k: r.get(col) for k, col in mapping.items()}
            rec.setdefault("state", default_state)
            if rec.get("arrival_date"):
                rec["arrival_date"] = parse_date(rec["arrival_date"]).strftime("%d/%m/%Y")
            records.append(rec)
    if commodity_filter:
        records = [r for r in records if str(r.get("commodity", "")).strip().lower() == commodity_filter.lower()]
    with tracked_run(db, "agmarknet_backfill") as run:
        counts = store_records(db, records, source="agmarknet_bulk")
        run.rows = counts["stored"] + counts["updated"]
        run.details = {**counts, "file": p.name}
        return run.details
