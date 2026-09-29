"""Feature-store config (config/features.toml) and feature_set naming."""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[3] / "config" / "features.toml"

# Groups that exist in the registry but are built in later phases.
PENDING = {"satellite": "V2-4 (Sentinel-2 crop signal)", "graph": "V2-3 (mandi graph + GNN)",
           "transit": "V2-5 (in-transit tonnage)"}


class FeatureGroupNotBuilt(NotImplementedError):
    pass


@lru_cache
def features_config() -> dict:
    path = Path(os.environ.get("FEATURES_CONFIG", _DEFAULT))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def lag(source: str) -> int:
    return int(features_config()["publication_lag_days"][source])


def parse_feature_set(name_or_groups) -> list[str]:
    """'prices+weather' or ['weather', 'prices'] -> canonical ordered list. Unknown groups raise."""
    order = features_config()["groups"]["order"]
    groups = name_or_groups.split("+") if isinstance(name_or_groups, str) else list(name_or_groups)
    groups = [g.strip() for g in groups if g.strip() and g.strip() != "calendar"]
    unknown = sorted(set(groups) - set(order))
    if unknown:
        raise ValueError(f"Unknown feature group(s) {unknown}; known: {order} (+ calendar, always on)")
    if not groups:
        raise ValueError("A feature set needs at least one group")
    return [g for g in order if g in groups]


def feature_set_name(groups) -> str:
    return "+".join(parse_feature_set(groups))


def check_built(groups: list[str]) -> None:
    pending = [g for g in groups if g in PENDING]
    if pending:
        raise FeatureGroupNotBuilt(
            "; ".join(f"'{g}' features are built in {PENDING[g]}" for g in pending) + ". Not available yet."
        )
