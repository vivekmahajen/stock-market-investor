"""Backtest robustness: out-of-sample, walk-forward, sensitivity, sub-periods (§8).

The spec is blunt about overfitting: distinguish in-sample from out-of-sample,
run walk-forward, check parameter sensitivity, and look across sub-periods. This
module supplies those checks on top of ``run_backtest`` and returns honest
verdicts — a good-looking in-sample curve that collapses out-of-sample is called
what it is.
"""
from __future__ import annotations

import itertools
import math
from typing import Callable, Dict, List, Optional

from .backtest import SignalFn, run_backtest
from .types import OHLCV

# A strategy factory turns a params dict into a SignalFn.
StrategyFactory = Callable[[dict], SignalFn]


def _metric_val(metrics: dict, name: str) -> float:
    v = metrics.get(name)
    if v is None:
        return 0.0
    return float(v)


def _grid(param_grid: Dict[str, list]) -> List[dict]:
    if not param_grid:
        return [{}]
    keys = list(param_grid.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*(param_grid[k] for k in keys))]


def _std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# --------------------------------------------------------------------------- #
# E1. In-sample / out-of-sample split
# --------------------------------------------------------------------------- #
def train_test_split(series: OHLCV, signal_fn: SignalFn, split: float = 0.7, **bt_kwargs) -> dict:
    """Backtest on the first ``split`` fraction (in-sample) and the rest
    (out-of-sample), reported separately with a hold-up assessment."""
    n = len(series)
    cut = int(n * split)
    if cut < 30 or n - cut < 30:
        return {"error": f"series too short to split ({n} bars) — need >=30 bars per side."}
    r_in = run_backtest(series.slice(0, cut), signal_fn, **bt_kwargs)
    r_out = run_backtest(series.slice(cut, n), signal_fn, **bt_kwargs)
    s_in = r_in.metrics["sharpe"]
    s_out = r_out.metrics["sharpe"]

    if s_out <= 0:
        verdict = "does NOT hold out-of-sample — likely in-sample overfit"
    elif s_out >= 0.5 * s_in:
        verdict = "holds out-of-sample — edge is plausibly real"
    else:
        verdict = "degrades out-of-sample — fragile, treat with caution"
    return {
        "split": split,
        "in_sample": r_in.metrics,
        "out_of_sample": r_out.metrics,
        "assessment": verdict,
    }


# --------------------------------------------------------------------------- #
# E2. Walk-forward (optimize on train window, test on the next)
# --------------------------------------------------------------------------- #
def walk_forward(
    series: OHLCV,
    strategy_factory: StrategyFactory,
    param_grid: Dict[str, list],
    n_folds: int = 4,
    metric: str = "sharpe",
    **bt_kwargs,
) -> dict:
    """Anchored walk-forward: for each fold, optimize params on all prior data,
    then evaluate on the held-out next window. Aggregates out-of-sample results
    and flags parameter instability (a sign of overfitting)."""
    n = len(series)
    combos = _grid(param_grid)
    block = n // (n_folds + 1)
    if block < 30:
        return {"error": f"series too short for {n_folds} walk-forward folds ({n} bars)."}

    folds: List[dict] = []
    oos_returns: List[float] = []
    best_param_sets = []
    for f in range(n_folds):
        test_start = block * (f + 1)
        test_end = n if f == n_folds - 1 else block * (f + 2)
        train = series.slice(0, test_start)
        test = series.slice(test_start, test_end)

        best_params, best_val = combos[0], -math.inf
        for params in combos:
            m = run_backtest(train, strategy_factory(params), **bt_kwargs).metrics
            val = _metric_val(m, metric)
            if val > best_val:
                best_val, best_params = val, params
        oos = run_backtest(test, strategy_factory(best_params), **bt_kwargs)
        best_param_sets.append(tuple(sorted(best_params.items())))
        oos_returns.append(oos.metrics["total_return_pct"])
        folds.append({
            "fold": f,
            "best_params": best_params,
            f"train_{metric}": round(best_val, 3),
            "oos_metrics": oos.metrics,
        })

    distinct = len(set(best_param_sets))
    positive = sum(1 for r in oos_returns if r > 0)
    stability = 1.0 - (distinct - 1) / max(1, n_folds - 1)  # 1.0 = same params every fold
    if positive >= n_folds - 1 and stability >= 0.5:
        verdict = "consistent out-of-sample with stable params — encouraging"
    elif positive >= n_folds / 2:
        verdict = "mixed out-of-sample — some edge, but params drift"
    else:
        verdict = "fails walk-forward — no persistent edge"
    return {
        "n_folds": n_folds,
        "folds": folds,
        "oos_positive_folds": positive,
        "param_stability": round(stability, 2),
        "avg_oos_return_pct": round(sum(oos_returns) / len(oos_returns), 2),
        "assessment": verdict,
    }


# --------------------------------------------------------------------------- #
# E3. Parameter sensitivity
# --------------------------------------------------------------------------- #
def parameter_sensitivity(
    series: OHLCV,
    strategy_factory: StrategyFactory,
    param_grid: Dict[str, list],
    metric: str = "sharpe",
    **bt_kwargs,
) -> dict:
    """Run every parameter combination and report the metric's distribution.

    A strategy whose result swings wildly across nearby parameters is
    curve-fit; one that's stable across the grid is more trustworthy.
    """
    combos = _grid(param_grid)
    rows = []
    for params in combos:
        m = run_backtest(series, strategy_factory(params), **bt_kwargs).metrics
        rows.append({"params": params, "value": round(_metric_val(m, metric), 3)})
    vals = [r["value"] for r in rows]
    mean = sum(vals) / len(vals)
    std = _std(vals)
    cv = std / abs(mean) if mean else math.inf
    best = max(rows, key=lambda r: r["value"])
    worst = min(rows, key=lambda r: r["value"])

    if cv < 0.5:
        verdict = "robust — metric is stable across the parameter grid"
    elif cv < 1.5:
        verdict = "sensitive — parameter choice matters; curve-fitting risk"
    else:
        verdict = "highly sensitive — result likely an overfit artifact"
    return {
        "metric": metric,
        "combinations": len(combos),
        "mean": round(mean, 3),
        "std": round(std, 3),
        "coef_of_variation": (round(cv, 3) if cv != math.inf else None),
        "best": best,
        "worst": worst,
        "assessment": verdict,
    }


# --------------------------------------------------------------------------- #
# E4. Sub-period analysis
# --------------------------------------------------------------------------- #
def sub_period_analysis(series: OHLCV, signal_fn: SignalFn, n_periods: int = 4, **bt_kwargs) -> dict:
    """Split the series into contiguous sub-periods and backtest each, to see
    whether the edge is consistent or driven by a single stretch."""
    n = len(series)
    size = n // n_periods
    if size < 30:
        return {"error": f"series too short for {n_periods} sub-periods ({n} bars)."}
    periods = []
    returns = []
    for i in range(n_periods):
        start = i * size
        end = n if i == n_periods - 1 else (i + 1) * size
        seg = series.slice(start, end)
        m = run_backtest(seg, signal_fn, **bt_kwargs).metrics
        periods.append({
            "period": i,
            "from": seg.ts[0].isoformat() if len(seg) else None,
            "to": seg.ts[-1].isoformat() if len(seg) else None,
            "total_return_pct": m["total_return_pct"],
            "sharpe": m["sharpe"],
            "num_trades": m["num_trades"],
        })
        returns.append(m["total_return_pct"])
    positive = sum(1 for r in returns if r > 0)
    consistency = positive / n_periods
    if consistency == 1.0:
        verdict = "profitable in every sub-period — consistent"
    elif consistency >= 0.5:
        verdict = "mixed across sub-periods — edge is regime-dependent"
    else:
        verdict = "unprofitable in most sub-periods — not a durable edge"
    return {
        "n_periods": n_periods,
        "periods": periods,
        "consistency": round(consistency, 2),
        "assessment": verdict,
    }
