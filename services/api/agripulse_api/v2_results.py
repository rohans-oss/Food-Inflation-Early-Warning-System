"""V2 (Intelligence) findings shown on the Admin page. Each one carries its data provenance; the numbers live in the
linked docs, which are generated from docs/results/*.csv. Update this list when a result is re-run on real data."""
from .provenance import LABEL, REAL, SYNTHETIC

RESULTS = [
    {"phase": "V2-0", "title": "Shared evaluation harness", "outcome": "finding",
     "headline": "V1's 'LightGBM beats naive' held on one synthetic draw only; across 8 draws there is no reliable edge.",
     "data_provenance": SYNTHETIC, "doc": "docs/backtest-synthetic.md"},
    {"phase": "V2-2", "title": "Temporal Fusion Transformer", "outcome": "negative",
     "headline": "15-33% worse than naive on pinball across 3 draws; raw intervals hold the price 46-59% of the time.",
     "data_provenance": SYNTHETIC, "doc": "docs/tft-results.md"},
    {"phase": "V2-3", "title": "Mandi graph + GNN", "outcome": "mixed",
     "headline": "Graph features help LightGBM 1-3% on every draw but only to parity with naive; the GNN loses 10-32%.",
     "data_provenance": SYNTHETIC, "doc": "docs/graph-results.md"},
    {"phase": "V2-4", "title": "Sentinel-2 crop signal vs tomato statistics", "outcome": "negative",
     "headline": "No evidence district cropland NDVI tracks tomato area or production beyond a shared trend "
                 "(r 0.60 -> -0.14 detrended, 2 districts, n = 10). The NDVI signal itself is sound.",
     "data_provenance": REAL, "doc": "docs/satellite-results.md"},
    {"phase": "V2-5", "title": "Ablation of feature groups", "outcome": "finding",
     "headline": "No group makes LightGBM reliably beat naive. Graph +1-3%, transit gain is built in (simulated), "
                 "satellite gain is seasonality; removing weather helped in 2 of 3 draws.",
     "data_provenance": SYNTHETIC, "doc": "docs/ablation-results.md"},
    {"phase": "V2-5", "title": "Real-data ablation", "outcome": "not enough real data",
     "headline": "Real prices start 2026-09-25; the harness needs about 13 months. Same command re-runs it then.",
     "data_provenance": REAL, "doc": "docs/ablation-results.md"},
]


def v2_results() -> list[dict]:
    return [{**r, "provenance_label": LABEL[r["data_provenance"]]} for r in RESULTS]
