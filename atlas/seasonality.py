"""Seasonality statistics (Section 3 ``compute_seasonality``).

Reports historical return statistics by calendar bucket *with sample sizes*.
Small buckets are flagged — a 2-sample "90% positive" month is noise.
"""
from __future__ import annotations

from typing import Dict, List

from .types import OHLCV

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def compute_seasonality(series: OHLCV, granularity: str = "month") -> dict:
    """Bucket per-bar returns by ``month`` or ``weekday`` and summarise each.

    Returns per-bucket mean return, % positive, and sample size, plus a warning
    listing buckets with fewer than 10 samples.
    """
    if granularity not in ("month", "weekday"):
        raise ValueError("granularity must be 'month' or 'weekday'")

    buckets: Dict[int, List[float]] = {}
    for i in range(1, len(series)):
        prev, cur = series.close[i - 1], series.close[i]
        if prev <= 0:
            continue
        ret = (cur / prev - 1.0) * 100.0
        key = series.ts[i].month - 1 if granularity == "month" else series.ts[i].weekday()
        buckets.setdefault(key, []).append(ret)

    labels = _MONTHS if granularity == "month" else _WEEKDAYS
    rows = []
    thin = []
    for key in sorted(buckets):
        rets = buckets[key]
        n = len(rets)
        mean = sum(rets) / n
        pos = sum(1 for r in rets if r > 0) / n * 100.0
        rows.append(
            {
                "bucket": labels[key],
                "mean_return_pct": round(mean, 3),
                "pct_positive": round(pos, 1),
                "samples": n,
            }
        )
        if n < 10:
            thin.append(labels[key])

    warnings = []
    if thin:
        warnings.append(
            f"Thin samples (<10) in: {', '.join(thin)} — those stats are unreliable."
        )
    return {"granularity": granularity, "buckets": rows, "warnings": warnings}
