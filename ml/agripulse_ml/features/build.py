"""Build a training table for any feature_set + data_provenance and print its data card.

    python -m agripulse_ml.features.build --feature-set prices+weather --provenance synthetic [--seed 7]
    python -m agripulse_ml.features.build --feature-set prices+weather --provenance real      # from DATABASE_URL

Writes data/feature_tables/<feature_set>__<provenance>[__seed<N>].parquet plus a .card.json next to it.
--provenance real reads only non-synthetic rows; the table's own provenance is then real or real_partial
per mandi (readiness monitor), and is printed on the card. No models are trained here.
"""
import argparse
import json
import sys
from pathlib import Path

from agripulse_api.provenance import LABEL

from .config import FeatureGroupNotBuilt, feature_set_name
from .inputs import Inputs
from .store import build_table

OUT = Path(__file__).resolve().parents[3] / "data" / "feature_tables"


def print_card(card: dict) -> None:
    print(f"\n=== {card['provenance_label']} ===")
    print(f"feature_set      {card['feature_set']}   (groups: {', '.join(card['groups'])})")
    print(f"provenance       {card['data_provenance']}   per group: {card['group_provenance']}")
    print(f"rows / mandis    {card['rows']:,} / {card['mandis']}")
    print(f"date range       {card['date_range']}")
    print(f"features         {card['n_features']}  static {len(card['columns']['static'])} · known-future "
          f"{len(card['columns']['known_future'])} · past-only {len(card['columns']['past_only'])}")
    print(f"target coverage  {card['target_coverage_pct']}   spike rate {card['spike_rate_pct']}%")
    print(f"publication lag  {card['publication_lag_days']}   label lag {card['label_lag_days']} d")
    worst = sorted(card["missing_pct"].items(), key=lambda kv: -kv[1])[:6]
    print("most missing     " + ", ".join(f"{k} {v}%" for k, v in worst))
    for n in card["notes"]:
        print(f"note             {n}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-set", default="prices+weather")
    ap.add_argument("--provenance", choices=["synthetic", "real"], required=True,
                    help="synthetic: pinned generator (or DB synthetic rows with --from-db); real: DB real rows")
    ap.add_argument("--seed", type=int, default=7, help="synthetic generator seed")
    ap.add_argument("--from-db", action="store_true", help="synthetic: read synthetic rows from the DB instead")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    if args.provenance == "synthetic" and not args.from_db:
        inputs = Inputs.from_synthetic(seed=args.seed)
    else:
        from agripulse_api.db import SessionLocal

        with SessionLocal() as db:
            inputs = Inputs.from_db(db, synthetic=args.provenance == "synthetic")
        if inputs.prices.empty:
            print(f"No {args.provenance} price rows in the database yet. Nothing to build.")
            return 2
    try:
        table = build_table(inputs, args.feature_set)
    except (FeatureGroupNotBuilt, ValueError) as exc:  # e.g. 'satellite' with no Sentinel-2 observations loaded
        print(f"Cannot build '{args.feature_set}': {exc}")
        return 2
    name = f"{feature_set_name(args.feature_set)}__{args.provenance}"
    if args.provenance == "synthetic" and not args.from_db:
        name += f"__seed{args.seed}"
    path, card_path = table.save(Path(args.out) / f"{name}.parquet")
    card = json.loads(card_path.read_text())
    print_card(card)
    print(f"\nwrote {path}\n      {card_path}")
    if table.data_provenance != "real":
        print(f"\n{LABEL[table.data_provenance]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
