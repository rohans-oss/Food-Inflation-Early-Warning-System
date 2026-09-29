"""V2 shared evaluation: one fold definition, one metric set, provenance on every row."""
from .folds import Fold, FoldSpec, NotEnoughHistory, make_folds, split
from .harness import RESULT_COLUMNS, EvalRun, legacy_metrics, run

__all__ = ["Fold", "FoldSpec", "NotEnoughHistory", "make_folds", "split", "RESULT_COLUMNS", "EvalRun", "legacy_metrics", "run"]
