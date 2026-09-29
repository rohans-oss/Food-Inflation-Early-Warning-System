"""The one walk-forward fold definition every model uses (V2 rule 10: same folds for every comparison).

For a fold with cutoff T:
  train = rows whose target date for the LONGEST horizon is < T   (no label leakage: every
          training label was observable before T)
  test  = rows issued in [T, T + step_days)
Folds are the last `n_folds` steps before the final issue date that still has a
max-horizon target, never earlier than start + min_train_days.
"""
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class FoldSpec:
    min_train_days: int = 365
    step_days: int = 28
    n_folds: int = 8
    max_horizon_weeks: int = 4
    min_train_rows: int = 200

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def target_date_col(self) -> str:
        return f"target_date_h{self.max_horizon_weeks}"


@dataclass(frozen=True)
class Fold:
    index: int
    cutoff: pd.Timestamp
    test_end: pd.Timestamp  # exclusive


class NotEnoughHistory(ValueError):
    pass


def make_folds(dates: pd.Series, spec: FoldSpec) -> list[Fold]:
    d = pd.to_datetime(pd.Series(dates)).sort_values()
    start, end = d.iloc[0], d.iloc[-1]
    last_issue = end - pd.Timedelta(days=7 * spec.max_horizon_weeks)  # later issues have no observed target
    first = max(start + pd.Timedelta(days=spec.min_train_days), last_issue - pd.Timedelta(days=spec.step_days * spec.n_folds))
    if first >= last_issue:
        raise NotEnoughHistory(
            f"Not enough history for a walk-forward backtest: {start.date()}..{end.date()} "
            f"(need > {spec.min_train_days} days + {7 * spec.max_horizon_weeks} days of targets)"
        )
    folds, T, i = [], first, 0
    while T < last_issue:
        T_end = min(T + pd.Timedelta(days=spec.step_days), last_issue)
        folds.append(Fold(i, T, T_end))
        T, i = T_end, i + 1
    return folds


def split(feat: pd.DataFrame, fold: Fold, spec: FoldSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = feat[feat[spec.target_date_col] < fold.cutoff]
    test = feat[(feat["date"] >= fold.cutoff) & (feat["date"] < fold.test_end)]
    return train, test
