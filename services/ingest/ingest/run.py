"""Run ingestion by hand.

    python -m ingest.run agmarknet            # today's live pull (needs DATA_GOV_API_KEY)
    python -m ingest.run open_meteo
    python -m ingest.run nasa_power --days 365
    python -m ingest.run backfill path/to/file.csv [--state Karnataka]
    python -m ingest.run synthetic            # DEV ONLY: labelled synthetic history
"""
import argparse
import json
from datetime import date, timedelta

from agripulse_api.db import SessionLocal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["agmarknet", "open_meteo", "nasa_power", "backfill", "synthetic"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--state", default="")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.job == "agmarknet":
            from .agmarknet import run_daily

            out = run_daily(db)
        elif args.job == "open_meteo":
            from .weather import run_open_meteo

            out = run_open_meteo(db, past_days=min(args.days, 92))
        elif args.job == "nasa_power":
            from .weather import run_nasa_power

            out = run_nasa_power(db, start=date.today() - timedelta(days=args.days))
        elif args.job == "backfill":
            if not args.path:
                ap.error("backfill needs a file path")
            from .agmarknet import import_file

            out = import_file(db, args.path, default_state=args.state)
        else:
            from agripulse_ml.synthetic import load_into_db

            out = load_into_db(db)
            print("NOTE: synthetic rows are tagged source='synthetic' and shown as such in the UI.")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
