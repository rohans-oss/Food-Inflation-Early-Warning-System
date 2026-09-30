"""Crops a lot can be (config/crops.toml). Forecasts exist only for crops with forecast = true (tomato)."""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[3] / "config" / "crops.toml"


@lru_cache
def crops() -> list[dict]:
    with open(Path(os.environ.get("CROPS_CONFIG", _DEFAULT)), "rb") as f:
        return tomllib.load(f)["crop"]


def names() -> set[str]:
    return {c["name"] for c in crops()}


def canonical(name: str) -> str | None:
    """Case-insensitive match to a configured crop name, or None."""
    k = name.strip().lower()
    return next((c["name"] for c in crops() if c["name"].lower() == k), None)


def has_forecast(name: str) -> bool:
    return any(c["name"] == name and c.get("forecast") for c in crops())
