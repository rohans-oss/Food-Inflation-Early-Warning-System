"""V1 walk-forward backtest API, now a thin wrapper over the V2 shared harness (agripulse_ml.eval).

Kept so V1 callers and tests keep working; the folds and metrics are the harness's, so V1
LightGBM and every V2 model are scored on exactly the same splits.
"""
from dataclasses import dataclass

from .eval import FoldSpec, legacy_metrics
from .eval import run as run_eval
from .eval.metrics import pinball  # noqa: F401  (re-exported for V1 imports)
from .models import LightGBMQuantileForecaster, NaiveForecaster, SeasonalNaiveForecaster, add_seasonal_naive

V1_MODELS = {
    "naive": NaiveForecaster,
    "seasonal_naive": SeasonalNaiveForecaster,
    "lightgbm_quantile": LightGBMQuantileForecaster,
}
V1_FEATURE_SET = "prices+weather"  # V1 features: price history + arrivals (past-only), weather, calendar


@dataclass
class BacktestConfig:
    min_train_days: int = 365
    step_days: int = 28
    n_folds: int = 8
    alert_probability: float = 0.5

    def spec(self) -> FoldSpec:
        return FoldSpec(min_train_days=self.min_train_days, step_days=self.step_days, n_folds=self.n_folds)


def walk_forward(feat, cfg: BacktestConfig | None = None, model_factories=None, data_provenance: str = "synthetic",
                 feature_set: str = V1_FEATURE_SET, return_run: bool = False):
    cfg = cfg or BacktestConfig()
    run = run_eval(feat, model_factories or V1_MODELS, feature_set=feature_set, data_provenance=data_provenance,
                   spec=cfg.spec(), alert_probability=cfg.alert_probability, prepare=add_seasonal_naive)
    report = {"folds": [{"start": f["cutoff"], "end": f["test_end"], "train_rows": f["train_rows"], "test_rows": f["test_rows"]}
                        for f in run.folds],
              "metrics": legacy_metrics(run.results), "n_predictions": int(len(run.predictions)),
              "data_provenance": run.data_provenance, "feature_set": run.feature_set, "run_id": run.run_id}
    return (report, run) if return_run else report
