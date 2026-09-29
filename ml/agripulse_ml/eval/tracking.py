"""Persist an EvalRun: always to the database (model_runs + eval_results), and to MLflow.

MLflow store: MLFLOW_TRACKING_URI if set, else a local SQLite store at mlruns/mlflow.db
(MLflow 3.x no longer accepts the plain ./mlruns folder store). Artifacts go next to it.
Set MLFLOW_DISABLE=1 to skip MLflow (the DB record is always written).
"""
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from agripulse_api.models import EvalResult, ModelRun
from agripulse_api.provenance import LABEL

from .harness import EvalRun

log = logging.getLogger("agripulse.eval")
DEFAULT_MLRUNS = Path(__file__).resolve().parents[3] / "mlruns"
EXPERIMENT = "agripulse-tomato"


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=Path(__file__).parent).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def record(db: Session, run: EvalRun, purpose: str = "", notes: str = "", mlflow_run_id: str | None = None) -> ModelRun:
    mr = ModelRun(
        run_id=run.run_id, models=run.model_names, feature_set=run.feature_set, data_provenance=run.data_provenance,
        fold_spec=run.spec.as_dict(), folds=run.folds, n_mandis=run.n_mandis, alert_probability=run.alert_probability,
        git_sha=git_sha(), mlflow_run_id=mlflow_run_id, purpose=purpose, notes=notes,
        started_at=run.started_at, finished_at=run.finished_at,
    )
    from datetime import date

    mr.data_start, mr.data_end = (date.fromisoformat(d) for d in run.data_range)
    db.add(mr)
    db.flush()
    r = run.results.astype(object).where(run.results.notna(), None)
    db.bulk_insert_mappings(EvalResult, [dict(run_id=run.run_id, **row) for row in r.to_dict("records")])
    db.commit()
    return mr


def log_mlflow(run: EvalRun, purpose: str = "", artifacts: dict[str, str] | None = None) -> str | None:
    """Returns the MLflow run id, or None when MLflow is disabled / not installed."""
    if os.environ.get("MLFLOW_DISABLE") == "1":
        return None
    try:
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        import mlflow
    except ImportError:
        log.warning("mlflow not installed; skipping MLflow logging")
        return None
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    artifact_root = None
    if not uri:
        DEFAULT_MLRUNS.mkdir(parents=True, exist_ok=True)
        uri = f"sqlite:///{DEFAULT_MLRUNS / 'mlflow.db'}"
        artifact_root = (DEFAULT_MLRUNS / "artifacts").as_uri()
    mlflow.set_tracking_uri(uri)
    exp = mlflow.get_experiment_by_name(EXPERIMENT)
    exp_id = exp.experiment_id if exp else mlflow.create_experiment(EXPERIMENT, artifact_location=artifact_root)
    pooled = run.results[run.results["mandi"] == "ALL"].dropna(subset=["metric_value"])
    with mlflow.start_run(experiment_id=exp_id, run_name=f"{purpose or 'eval'}-{run.feature_set}-{run.data_provenance}") as r:
        mlflow.set_tags({"data_provenance": run.data_provenance, "provenance_label": LABEL[run.data_provenance],
                         "feature_set": run.feature_set, "purpose": purpose, "agripulse_run_id": run.run_id,
                         "git_sha": git_sha() or ""})
        mlflow.log_params({"models": ",".join(run.model_names), **{f"fold.{k}": v for k, v in run.spec.as_dict().items()},
                           "n_folds_used": len(run.folds), "data_start": run.data_range[0], "data_end": run.data_range[1],
                           "n_mandis": run.n_mandis, "alert_probability": run.alert_probability})
        mlflow.log_metrics({f"{m}.h{h}.{k}": float(v) for m, h, k, v in
                            pooled[["model_name", "horizon", "metric_name", "metric_value"]].itertuples(index=False)})
        for name, path in (artifacts or {}).items():
            mlflow.log_artifact(path, artifact_path=name)
        return r.info.run_id
