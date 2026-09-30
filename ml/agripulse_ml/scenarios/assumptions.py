"""Channel B: a documented assumption chain. Supply shock -> price, with ranges.

    price multiplier = (1 + supply change) ** elasticity          (log-log, RBI WPS 08/2024)

rainfall_failure, mandi in the region, target date inside the affected harvest window:
    supply change = - ky x deficit x unreplaced_rain_share x local_supply_share
export_ban, target date on/after the ban starts:
    supply change = + export share (national exports / production, or a user-set regional share)

Low / central / high: every combination of the uncertain inputs is evaluated; the band is the min..max multiplier,
applied as p10 x min, p50 x central, p90 x max. In relative terms (p90/p10) the band therefore never narrows: the
scenario's own uncertainty is added to the forecast's."""
import itertools
from datetime import date, timedelta

from . import scenario_config


def _levels(sec: dict, name: str) -> dict:
    return {k: sec[f"{name}_{k}"] for k in ("low", "central", "high")}


def elasticities(cfg: dict | None = None) -> dict:
    return _levels((cfg or scenario_config())["demand"], "elasticity")


def affected_window(start: date, end: date, cfg: dict | None = None) -> tuple[date, date]:
    r = (cfg or scenario_config())["rainfall_failure"]
    return start + timedelta(days=r["harvest_lag_min_days"]), end + timedelta(days=r["harvest_lag_max_days"])


def rainfall_supply_changes(deficit: float, cfg: dict | None = None) -> dict:
    """Supply change (fraction) for an affected mandi: central + every combination of the uncertain inputs."""
    r = (cfg or scenario_config())["rainfall_failure"]
    ky, unrep, local = _levels(r, "ky"), _levels(r, "unreplaced_rain_share"), _levels(r, "local_supply_share")
    f = lambda k, u, s: -min(1.0, k * deficit * u * s)  # noqa: E731  (can't lose more than all local supply)
    return {"central": f(ky["central"], unrep["central"], local["central"]),
            "all": [f(k, u, s) for k, u, s in itertools.product(ky.values(), unrep.values(), local.values())]}


def export_share(cfg: dict | None = None) -> float:
    e = (cfg or scenario_config())["export_ban"]
    return e["exports_tonnes"] / e["production_tonnes"]


def multipliers(supply: dict, cfg: dict | None = None) -> dict:
    """Price multipliers: central (central supply x central elasticity), low / high over every combination."""
    el = elasticities(cfg)
    allm = [(1.0 + s) ** e for s in supply["all"] for e in el.values()]
    return {"central": (1.0 + supply["central"]) ** el["central"], "low": min(allm), "high": max(allm)}


def shift(p10: float, p50: float, p90: float, m: dict) -> dict:
    """Apply multipliers to a forecast range; p90/p10 never shrinks."""
    return {"p10": p10 * min(m["low"], m["central"]), "p50": p50 * m["central"], "p90": p90 * max(m["high"], m["central"])}
