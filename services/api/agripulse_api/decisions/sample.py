"""Seeded sample decision problems (lots around Karnataka tomato belts, trucks in fleet towns).
Used by the V3-0 study (ml/agripulse_ml/decision_study.py) and the Admin "compare recommenders" card, so both run
exactly the same scenarios. These are SIMULATED lots and trucks, never real ones."""
import numpy as np

from .model import Lot, Vehicle

DENSITY = {"sparse": (6, 8, None), "medium": (20, 18, None), "dense_tight": (60, None, 0.70)}
# Karnataka tomato belts (lot pickups) and fleet towns (vehicle bases): approximate centres, jittered per lot
BELTS = [(13.14, 78.13), (13.40, 78.06), (13.17, 78.39), (13.43, 77.73), (14.23, 76.40), (14.79, 75.40),
         (15.85, 74.50), (12.10, 76.70)]
FLEET_TOWNS = [(13.14, 78.13), (13.43, 77.73), (12.97, 77.59), (14.23, 76.40), (15.36, 75.12)]
CAPACITIES, CAP_P = (2.5, 5.0, 9.0, 10.0), (0.3, 0.4, 0.2, 0.1)


def scenario(rng: np.random.Generator, density: str) -> tuple[list[Lot], list[Vehicle]]:
    """sparse: 6 lots / 8 trucks; medium: 20 / 18; dense_tight: 60 lots, trucks for ~70% of the tonnage."""
    n_lots, n_veh, cap_ratio = DENSITY[density]
    lots = []
    for i in range(n_lots):
        c = BELTS[rng.integers(len(BELTS))]
        t = float(np.clip(np.round(rng.lognormal(np.log(3.0), 0.6), 1), 0.5, 9.0))
        lots.append(Lot(i, c[0] + rng.normal(0, 0.12), c[1] + rng.normal(0, 0.12), t))
    need = sum(x.tons for x in lots) * cap_ratio if cap_ratio else None
    vehicles, total = [], 0.0
    while (need is None and len(vehicles) < n_veh) or (need is not None and total < need):
        c = FLEET_TOWNS[rng.integers(len(FLEET_TOWNS))]
        cap = float(rng.choice(CAPACITIES, p=CAP_P))
        vehicles.append(Vehicle(len(vehicles), cap, c[0] + rng.normal(0, 0.03), c[1] + rng.normal(0, 0.03)))
        total += cap
    return lots, vehicles
