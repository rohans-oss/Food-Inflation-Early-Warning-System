"""Cleaning layer for Agmarknet rows.

- standardises mandi / commodity names and maps raw spellings onto canonical mandis
- parses DD/MM/YYYY dates and numeric strings
- flags (never silently drops) suspicious prices:
    min_gt_max        min_price > max_price (seen in real data, e.g. Belgaum 25/09/2026)
    modal_outside     modal not within [min, max]
    non_positive      modal <= 0
    jump_vs_history   modal is > 5x above/below the mandi's trailing 30-day median
  A row with any flag except `min_gt_max` alone is marked is_outlier=True; the
  forecaster excludes outliers and the Admin data-quality page counts them.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.models import Mandi

_WS = re.compile(r"\s+")


def norm_key(name: str) -> str:
    """Comparison key: lowercase, punctuation-insensitive, collapsed spaces."""
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _WS.sub(" ", s).strip()
    # "xyz apmc" and "xyz" refer to the same market yard; spacing never matters
    return s.removesuffix(" apmc").strip().replace(" ", "")


def clean_text(v) -> str:
    return _WS.sub(" ", str(v or "")).strip()


def normalize_commodity(raw: str) -> str:
    s = clean_text(raw)
    return s[:1].upper() + s[1:].lower() if s else s


def parse_date(v) -> date:
    if isinstance(v, date):
        return v
    s = clean_text(v)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {v!r}")


def parse_num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = clean_text(v).replace(",", "")
    if s in ("", "-", "NA", "N/A", "nan"):
        return None
    return float(s)


class MandiResolver:
    """Maps raw Agmarknet market names to canonical Mandi rows, creating unknown ones
    (without coordinates, so they stay off maps until an admin verifies them)."""

    def __init__(self, db: Session):
        self.db = db
        self._by_key: dict[str, Mandi] = {}
        for m in db.scalars(select(Mandi)):
            for n in [m.name, *(m.aliases or [])]:
                self._by_key[norm_key(n)] = m
        self.created: list[str] = []

    def resolve(self, market: str, district: str, state: str) -> Mandi:
        raw = clean_text(market)
        key = norm_key(raw)
        m = self._by_key.get(key)
        if m is None:
            m = Mandi(name=raw, district=clean_text(district), state=clean_text(state), aliases=[raw])
            self.db.add(m)
            self.db.flush()
            self._by_key[key] = m
            self.created.append(raw)
        elif raw not in (m.aliases or []) and raw != m.name:
            m.aliases = [*(m.aliases or []), raw]
        return m


@dataclass
class CleanRow:
    market: str
    district: str
    state: str
    commodity: str
    variety: str
    grade: str
    date: date
    min_price: float | None
    max_price: float | None
    modal_price: float
    flags: list[str] = field(default_factory=list)

    @property
    def is_outlier(self) -> bool:
        return any(f != "min_gt_max" for f in self.flags)


def clean_record(r: dict) -> CleanRow | None:
    """Returns None for rows that cannot be used at all (no modal price / no date)."""
    modal = parse_num(r.get("modal_price"))
    if modal is None or not r.get("arrival_date"):
        return None
    row = CleanRow(
        market=clean_text(r.get("market")),
        district=clean_text(r.get("district")),
        state=clean_text(r.get("state")),
        commodity=normalize_commodity(r.get("commodity", "")),
        variety=clean_text(r.get("variety")),
        grade=clean_text(r.get("grade")),
        date=parse_date(r["arrival_date"]),
        min_price=parse_num(r.get("min_price")),
        max_price=parse_num(r.get("max_price")),
        modal_price=modal,
    )
    lo, hi = row.min_price, row.max_price
    if lo is not None and hi is not None and lo > hi:
        row.flags.append("min_gt_max")
    if modal <= 0:
        row.flags.append("non_positive")
    elif lo is not None and hi is not None and not (min(lo, hi) <= modal <= max(lo, hi)):
        row.flags.append("modal_outside")
    return row


def history_jump_flag(modal: float, history: list[float], max_ratio: float = 5.0) -> bool:
    """True when the price is more than `max_ratio` x above or below the trailing 30-day
    median (needs >= 7 points). Deliberately loose: tomato legitimately doubles or
    halves within weeks, and those moves are exactly what we forecast. This only
    catches unit / typo errors (e.g. Rs/kg entered as Rs/quintal, extra zero)."""
    if len(history) < 7 or modal <= 0:
        return False
    med = float(np.median(np.asarray(history, dtype=float)))
    if med <= 0:
        return False
    return abs(np.log(modal / med)) > np.log(max_ratio)
