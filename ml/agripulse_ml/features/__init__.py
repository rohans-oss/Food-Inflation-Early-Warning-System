"""Feature engineering.

V1 (unchanged, the baseline every V2 model is compared against):
    from agripulse_ml.features import build_features, FEATURES      -> features/legacy.py
V2 feature store (groups, publication lags, known-future vs past-only):
    from agripulse_ml.features.store import build_table              -> features/store.py
"""
from .legacy import (  # noqa: F401  (V1 public API, re-exported so existing imports keep working)
    FEATURES,
    FESTIVAL_WINDOWS,
    HORIZONS,
    MAX_FFILL_DAYS,
    QUANTILES,
    SPIKE_WINDOW_DAYS,
    build_features,
    daily_grid,
    festival_flag,
    season_code,
)
