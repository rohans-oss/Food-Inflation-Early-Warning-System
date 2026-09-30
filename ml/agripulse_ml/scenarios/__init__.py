"""V3-2 scenario simulator. COUNTERFACTUAL ESTIMATES — not a validated causal model (rule 22).

Each scenario is computed by two SEPARATE channels, never blended:
  A. model.py       "what the current model does": perturb the display model's own inputs (rain / arrivals), re-run it.
                    A sensitivity of a model trained on SYNTHETIC data - it can be flat or counter-intuitive.
  B. assumptions.py "documented assumption chain": supply shock -> price via a sourced demand elasticity, with
                    low / central / high ranges (config/scenarios.toml, docs/scenario-assumptions.md).
run.py stores a run in scenario_runs; the forecasts table is never written.
"""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "config" / "scenarios.toml"
SCENARIOS = ("rainfall_failure", "export_ban")


@lru_cache
def scenario_config() -> dict:
    with open(os.environ.get("SCENARIOS_CONFIG", CONFIG), "rb") as f:
        return tomllib.load(f)


def label() -> str:
    return scenario_config()["label"]["text"]
