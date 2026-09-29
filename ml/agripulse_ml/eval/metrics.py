"""Forecast metrics, all in price space (Rs/quintal) so every model is scored identically.

interval metrics (per horizon):
    pinball_p10 / pinball_p50 / pinball_p90   mean quantile loss (lower is better)
    pinball_mean                              mean of the three
    mape_p50_pct                              mean |p50 - y| / y, in %
    coverage_p10_p90_pct                      % of outcomes inside [p10, p90]; target 80
spike metrics (14-day spike label, alert when prob >= threshold):
    spike_recall            alerts on real spikes / real spikes
    spike_precision         alerts on real spikes / all alerts
    spike_false_alarm_rate  alerts on non-spike days / non-spike days
    spike_brier             mean (prob - label)^2
"""
import numpy as np

QUANTILES = (0.1, 0.5, 0.9)


def pinball(y: np.ndarray, pred: np.ndarray, q: float) -> np.ndarray:
    d = np.asarray(y, float) - np.asarray(pred, float)
    return np.maximum(q * d, (q - 1) * d)


def interval_metrics(y: np.ndarray, p10: np.ndarray, p50: np.ndarray, p90: np.ndarray) -> dict[str, float]:
    y, p10, p50, p90 = (np.asarray(a, float) for a in (y, p10, p50, p90))
    n = len(y)
    if n == 0:
        return {"n": 0}
    pins = {f"pinball_p{int(q * 100)}": float(pinball(y, p, q).mean()) for q, p in zip(QUANTILES, (p10, p50, p90))}
    return {
        **pins,
        "pinball_mean": float(np.mean(list(pins.values()))),
        "mape_p50_pct": float(np.mean(np.abs(p50 - y) / y) * 100),
        "coverage_p10_p90_pct": float(np.mean((y >= p10) & (y <= p90)) * 100),
        "n": n,
    }


def spike_metrics(truth: np.ndarray, prob: np.ndarray, threshold: float) -> dict[str, float | None]:
    truth = np.asarray(truth).astype(int)
    prob = np.asarray(prob, float)
    alert = prob >= threshold
    pos, neg = truth == 1, truth == 0
    tp, fp = int((alert & pos).sum()), int((alert & neg).sum())
    return {
        "spike_events": int(pos.sum()),
        "spike_recall": tp / pos.sum() if pos.sum() else None,
        "spike_precision": tp / alert.sum() if alert.sum() else None,
        "spike_false_alarm_rate": fp / neg.sum() if neg.sum() else None,
        "spike_brier": float(np.mean((prob - truth) ** 2)) if len(truth) else None,
    }
