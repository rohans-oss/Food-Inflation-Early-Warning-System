"""Crops a lot can be: config/crops.toml plus vegetables farmers added (custom_crops). Price FORECASTS exist only for
crops with forecast = true (tomato). Real prices come from Agmarknet for any crop whose feed name we pull."""
import os
import re
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
    """Case-insensitive match to a configured crop name (or its Agmarknet name), or None."""
    k = normalize(name).lower()
    return next((c["name"] for c in crops() if k in {c["name"].lower(), normalize(c.get("agmarknet", "")).lower()}), None)


def has_forecast(name: str) -> bool:
    return any(c["name"] == name and c.get("forecast") for c in crops())


def normalize(raw: str | None) -> str:
    """The form prices.commodity is stored in (same rule as ingest.cleaning.normalize_commodity)."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    return s[:1].upper() + s[1:].lower() if s else s


def config_feed_name(name: str) -> str | None:
    c = next((c for c in crops() if c["name"] == name), None)
    return normalize(c.get("agmarknet") or c["name"]) if c else None


# ------------------------------------------------------------------ DB-backed (custom crops, live feed names)


def feed_name(db, name: str) -> str | None:
    """prices.commodity value for a crop (config or custom), or None when the feed has no such commodity."""
    fn = config_feed_name(name)
    if fn:
        return fn
    from sqlalchemy import select

    from .models import CustomCrop

    cc = db.scalar(select(CustomCrop).where(CustomCrop.name == name))
    return cc.feed_name if cc else None


def tracked_feed_names(db) -> set[str]:
    """Commodities whose prices the daily job stores: every configured crop + every custom crop with a feed match."""
    from sqlalchemy import select

    from .models import CustomCrop

    out = {config_feed_name(c["name"]) for c in crops()}
    out |= {n for n in db.scalars(select(CustomCrop.feed_name).where(CustomCrop.feed_name.is_not(None)))}
    return out


def resolve(db, raw: str, user_id: int | None = None, create: bool = False) -> tuple[str | None, bool]:
    """Name a lot is stored under for what the farmer picked/typed. Returns (name, created_now).
    Config crop -> its name. Known custom crop -> its name. Otherwise, with create=True, a new custom crop, matched to
    a live-feed commodity when one has that name."""
    c = canonical(raw)
    if c:
        return c, False
    from sqlalchemy import func, select

    from .models import CustomCrop, FeedCommodity

    clean = normalize(raw)[:60]
    if len(clean) < 2 or not re.fullmatch(r"[\w .,()&'/-]+", clean):
        return None, False
    cc = db.scalar(select(CustomCrop).where(func.lower(CustomCrop.name) == clean.lower()))
    if cc:
        return cc.name, False
    fc = db.scalar(select(FeedCommodity).where(func.lower(FeedCommodity.name) == clean.lower()))
    if not create:
        return None, False
    cc = CustomCrop(name=fc.name if fc else clean, feed_name=fc.name if fc else None, created_by_id=user_id)
    db.add(cc)
    db.flush()
    return cc.name, True


def all_crops(db) -> list[dict]:
    """Every crop a farmer can pick: config first, then custom ones."""
    from sqlalchemy import select

    from .models import CustomCrop

    out = [{"name": c["name"], "kn": c.get("kn"), "hi": c.get("hi"), "forecast": bool(c.get("forecast")),
            "custom": False, "feed_name": config_feed_name(c["name"])} for c in crops()]
    for cc in db.scalars(select(CustomCrop).order_by(CustomCrop.name)):
        out.append({"name": cc.name, "kn": None, "hi": None, "forecast": False, "custom": True, "feed_name": cc.feed_name})
    return out
