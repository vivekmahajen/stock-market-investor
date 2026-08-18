"""Horizon price-distribution forecasting with an honest skill check.

This is the quantitative core of the ATLAS Daily Report. It projects a symbol's
price distribution over a horizon and — critically — measures whether the model
has any edge over a random walk *on that symbol's own history*, so the report can
downgrade a point forecast to a mere reference level when the model has no skill.

Design commitments (mirroring the daily-report spec):

* A forecast is a **distribution**, never a target. Every result carries its
  horizon, its 80% and 95% intervals, and P(up) — not just a median.
* Numbers come from the series, not from the imagination. If history is too short
  to fit the model, the forecaster refuses (returns an ``error``) rather than
  emitting a fabricated band.
* The model is scored against a **naive random-walk baseline** by walk-forward
  evaluation. ``skill_score = 1 - mae_model / mae_naive``; zero or negative means
  no edge, and the report is expected to say so on that row.

The model is a lognormal drift/diffusion projection: log returns are assumed
roughly normal with a drift (mean log return) and volatility (std of log returns)
estimated from a trailing window. The horizon is converted from calendar days to
trading days via ``trading_ratio`` (~0.69 trading days per calendar day).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# Standard-normal quantiles for the reported intervals.
_Z80 = 1.2815515594  # P(|Z| < z) = 0.80  -> two-sided 80%
_Z95 = 1.9599639845  # two-sided 95%
_TRADING_RATIO = 0.69  # ~252 trading days / 365 calendar days


def _log_returns(closes: Sequence[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (stdlib, no SciPy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def horizon_trading_days(horizon_days: int, trading_ratio: float = _TRADING_RATIO) -> int:
    """Convert a calendar-day horizon to whole trading days (at least 1)."""
    return max(1, int(round(horizon_days * trading_ratio)))


def _project(last_close: float, mu: float, sigma: float, steps: int) -> Dict[str, object]:
    """Lognormal projection of ``last_close`` forward ``steps`` periods.

    ``mu``/``sigma`` are per-period (per-trading-day) mean and std of log returns.
    Returns the median (not the mean — the median of a lognormal is the geometric
    projection), symmetric log-space intervals, and P(up) over the horizon.
    """
    drift = mu * steps
    vol = sigma * math.sqrt(steps)
    median = last_close * math.exp(drift)

    def band(z: float) -> List[float]:
        return [last_close * math.exp(drift - z * vol), last_close * math.exp(drift + z * vol)]

    lo80, hi80 = band(_Z80)
    lo95, hi95 = band(_Z95)
    # P(end > start) = P(sum of log returns > 0) under N(drift, vol^2).
    p_up = _norm_cdf(drift / vol) if vol > 0 else (1.0 if drift > 0 else 0.0)
    return {
        "median": median,
        "interval_80": [lo80, hi80],
        "interval_95": [lo95, hi95],
        "p_up": p_up,
        "expected_return": median / last_close - 1.0,
        "drift_log": drift,
        "vol_log": vol,
    }


def _walk_forward_skill(closes: Sequence[float], steps: int, window: int,
                        max_folds: int = 60) -> Optional[Dict[str, object]]:
    """Measure out-of-sample forecast error vs. a random-walk baseline.

    At each anchor ``t`` (with at least ``window`` prior returns), fit
    drift/vol on the trailing window, project ``steps`` ahead, and compare the
    forecast median to the realised close ``steps`` later. The naive baseline is
    "price does not change" (a random walk with no drift), whose forecast is the
    anchor close itself. Also tracks directional accuracy and 80%-interval
    coverage.

    Returns ``None`` when there are too few folds to say anything.
    """
    n = len(closes)
    logret = [None] * n
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            logret[i] = math.log(closes[i] / closes[i - 1])

    first_anchor = window + 1
    last_anchor = n - 1 - steps
    if last_anchor < first_anchor:
        return None

    anchors = list(range(first_anchor, last_anchor + 1))
    # Cap the number of folds (evenly sampled) to keep the check bounded.
    if len(anchors) > max_folds:
        stride = len(anchors) / max_folds
        anchors = [anchors[int(k * stride)] for k in range(max_folds)]

    ae_model: List[float] = []
    ae_naive: List[float] = []
    dir_hits = 0
    covered = 0
    folds = 0
    for t in anchors:
        win = [r for r in logret[t - window + 1:t + 1] if r is not None]
        if len(win) < max(5, window // 2):
            continue
        mu, sigma = _mean(win), _std(win)
        anchor_close = closes[t]
        realised = closes[t + steps]
        if anchor_close <= 0 or realised <= 0:
            continue
        proj = _project(anchor_close, mu, sigma, steps)
        model_pred = proj["median"]
        ae_model.append(abs(model_pred - realised))
        ae_naive.append(abs(anchor_close - realised))
        # Directional accuracy: did the model get the sign of the move right?
        pred_up = model_pred >= anchor_close
        real_up = realised >= anchor_close
        if pred_up == real_up:
            dir_hits += 1
        lo80, hi80 = proj["interval_80"]
        if lo80 <= realised <= hi80:
            covered += 1
        folds += 1

    if folds < 3:
        return None

    mae_model = _mean(ae_model)
    mae_naive = _mean(ae_naive)
    skill = (1.0 - mae_model / mae_naive) if mae_naive > 0 else None
    return {
        "folds": folds,
        "mae_model": mae_model,
        "mae_naive": mae_naive,
        "skill_score": skill,
        "beats_random_walk": bool(skill is not None and skill > 0),
        "directional_accuracy": dir_hits / folds,
        "coverage_80": covered / folds,
        "nominal_coverage_80": 0.80,
        "in_sample": False,
        "window": window,
        "steps": steps,
    }


def forecast_price(
    closes: Sequence[float],
    horizon_days: int = 30,
    method: str = "drift",
    with_skill: bool = True,
    trading_ratio: float = _TRADING_RATIO,
    window: int = 120,
    last_close: Optional[float] = None,
) -> Dict[str, object]:
    """Forecast a symbol's price distribution ``horizon_days`` calendar days out.

    Parameters
    ----------
    closes:
        The trailing close series (oldest first). Must have enough history to fit
        the model and, if ``with_skill``, to walk-forward evaluate it.
    horizon_days:
        Forecast horizon in **calendar** days (default 30). Converted to trading
        days internally.
    method:
        ``"drift"`` (default) fits both drift and volatility from the window.
        ``"zero_drift"`` (a.k.a. random walk) keeps volatility but sets drift to
        zero — the honest baseline when a symbol has no reliable trend.
    with_skill:
        Run the walk-forward skill check. Costs extra compute but is what lets the
        report state measured error instead of asserting confidence.

    Returns a dict with ``median``, ``interval_80``, ``interval_95``, ``p_up``,
    ``expected_return``, the model ``inputs``, and (if requested) ``skill``. On
    insufficient history it returns ``{"error": ...}`` rather than a guess.
    """
    closes = [float(c) for c in closes if c is not None]
    if len(closes) < 2:
        return {"error": "forecast_price: need at least 2 closes"}
    ref = float(last_close) if last_close is not None else closes[-1]
    if ref <= 0:
        return {"error": "forecast_price: non-positive last close"}

    steps = horizon_trading_days(horizon_days, trading_ratio)
    eff_window = min(window, len(closes) - 1)
    if eff_window < 20:
        return {
            "error": (
                f"forecast_price: history too short ({len(closes)} closes) — "
                f"need >21 to estimate drift/volatility"
            )
        }

    win = _log_returns(closes[-(eff_window + 1):])
    if len(win) < 20:
        return {"error": "forecast_price: too few usable log returns"}
    mu = _mean(win)
    sigma = _std(win)
    if method in ("zero_drift", "random_walk", "naive"):
        mu = 0.0
    if sigma <= 0:
        return {"error": "forecast_price: zero volatility (flat history) — no distribution to project"}

    proj = _project(ref, mu, sigma, steps)
    result: Dict[str, object] = {
        "method": method,
        "horizon_days": horizon_days,
        "horizon_trading_days": steps,
        "last_close": ref,
        "median": proj["median"],
        "interval_80": proj["interval_80"],
        "interval_95": proj["interval_95"],
        "p_up": proj["p_up"],
        "expected_return": proj["expected_return"],
        "inputs": {
            "window_bars": eff_window,
            "daily_drift_log": mu,
            "daily_vol_log": sigma,
            "annualized_vol_pct": sigma * math.sqrt(252) * 100.0,
        },
        "skill": None,
        "in_sample": True,  # the distribution's own params are fit in-sample
    }
    if with_skill:
        result["skill"] = _walk_forward_skill(closes, steps, eff_window)
    return result
