"""config/models.toml loader."""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[3] / "config" / "models.toml"


@lru_cache
def models_config() -> dict:
    path = Path(os.environ.get("MODELS_CONFIG", _DEFAULT))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_path"] = str(path)
    return cfg


def display_model() -> str:
    return models_config()["display"]["model"]
