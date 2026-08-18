"""Horizon price-distribution forecasting with an honest skill check.

This is the quantitative core of the ATLAS Daily Report and the `forecast`
command. It projects a symbol's price distribution over a horizon and — critically
— measures whether the model has any edge over a random walk *on that symbol's
own history*, so the report can downgrade a point forecast to a mere reference
level when the model has no skill.

Design commitments (mirroring §21 of the system prompt):

* A forecast is a **distribution**, never a target. Every result carries its
  horizon, its 80% and 95% intervals, and P(up) — not just a median.
* Numbers come from the series, not from the imagination. Under 60 bars, or on a
  flat/degenerate series, the forecaster **refuses** (returns an ``error``)
  rather than emitting a fabricated band.
* **Volatility** is estimated by EWMA (RiskMetrics λ=0.94) so it tracks the
  current regime instead of averaging a year of calm and crisis together.
* **Drift** is the mean log return *shrunk toward zero* by its own
  signal-to-noise ratio — a sample mean of daily returns is mostly noise — and
  then **hard-capped at one horizon standard deviation** so a trend can never
  dominate the projection.
* The horizon distribution is lognormal: the headline is the **median**; the
  distribution's (higher) **mean** is reported separately and never conflated.
* The model is scored against a **naive random-walk baseline** by walk-forward
  evaluation. ``skill_score = 1 - mae_model / mae_naive``; zero or negative means
  no edge, and the report is expected to say so.

Methods:

* ``naive``  — random walk: zero drift, EWMA volatility only.
* ``drift``  — shrunk, capped historical drift (the default).
* ``blend``  — drift plus a capped short-window momentum tilt.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# Standard-normal quantiles for the reported intervals.
_Z80 = 1.2815515594  # two-sided 80%
_Z95 = 1.9599639845  # two-sided 95%
_TRADING_RATIO = 0.69  # ~252 trading days / 365 calendar days
_MIN_BARS = 60         # §21: refuse under 60 bars
_EWMA_LAMBDA = 0.94    # RiskMetrics decay for volatility
_MOMENTUM_WINDOW = 20  # short window for the blend tilt

_METHOD_ALIASES = {"random_walk": "naive", "zero_drift": "naive"}


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


def _ewma_vol(logret: Sequence[float], lam: float = _EWMA_LAMBDA) -> float:
    """Per-period volatility via an EWMA of squared log returns.

    Seeds the recursion with the sample variance of the first chunk so the early
    estimate is not dominated by a single return, then decays with ``lam``.
    """
    n = len(logret)
    if n < 2:
        return 0.0
    seed = logret[: min(n, 20)]
    var = _std(seed) ** 2 or (seed[0] ** 2 if seed else 0.0)
    for r in logret[1:]:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var)


def _shrunk_drift(logret: Sequence[float]) -> Dict[str, float]:
    """Mean log return shrunk toward zero by its signal-to-noise ratio.

    ``shrink = t^2 / (t^2 + 1)`` where ``t`` is the mean's t-statistic. When the
    drift is many standard errors from zero the factor → 1 (keep it); when the
    drift is within the noise the factor → 0 (a daily mean return is mostly
    noise, so it is discounted, not trusted).
    """
    n = len(logret)
    raw = _mean(logret)
    sd = _std(logret)
    if n < 2 or sd == 0.0:
        return {"raw": raw, "shrunk": 0.0, "shrink_factor": 0.0, "t_stat": 0.0}
    se = sd / math.sqrt(n)
    t = raw / se if se > 0 else 0.0
    factor = (t * t) / (t * t + 1.0)
    return {"raw": raw, "shrunk": raw * factor, "shrink_factor": factor, "t_stat": t}


def _fit(logret: Sequence[float], method: str) -> Dict[str, float]:
    """Estimate per-period (drift, sigma) for a method from a window of returns."""
    method = _METHOD_ALIASES.get(method, method)
    sigma = _ewma_vol(logret)
    drift_info = _shrunk_drift(logret)
    if method == "naive":
        drift = 0.0
    elif method == "blend":
        mom = _mean(logret[-_MOMENTUM_WINDOW:]) if len(logret) >= _MOMENTUM_WINDOW else drift_info["shrunk"]
        # Cap the momentum tilt at one per-period sigma before blending.
        mom = max(-sigma, min(sigma, mom))
        drift = 0.5 * drift_info["shrunk"] + 0.5 * mom
    else:  # "drift" (default)
        drift = drift_info["shrunk"]
    return {"drift": drift, "sigma": sigma, **{f"drift_{k}": v for k, v in drift_info.items()}}


def _project(last_close: float, drift_step: float, sigma_step: float, steps: int) -> Dict[str, object]:
    """Lognormal projection with the horizon drift capped at one horizon sigma.

    Returns the median (geometric projection), the (higher) mean, symmetric
    log-space intervals, P(up), and whether the cap bound the drift.
    """
    hvol = sigma_step * math.sqrt(steps)
    hdrift_raw = drift_step * steps
    capped = abs(hdrift_raw) > hvol
    hdrift = max(-hvol, min(hvol, hdrift_raw)) if hvol > 0 else 0.0

    median = last_close * math.exp(hdrift)
    mean = last_close * math.exp(hdrift + 0.5 * hvol * hvol)

    def band(z: float) -> List[float]:
        return [last_close * math.exp(hdrift - z * hvol), last_close * math.exp(hdrift + z * hvol)]

    lo80, hi80 = band(_Z80)
    lo95, hi95 = band(_Z95)
    p_up = _norm_cdf(hdrift / hvol) if hvol > 0 else (1.0 if hdrift > 0 else 0.0)
    return {
        "median": median,
        "mean": mean,
        "interval_80": [lo80, hi80],
        "interval_95": [lo95, hi95],
        "p_up": p_up,
        "expected_return": median / last_close - 1.0,
        "mean_return": mean / last_close - 1.0,
        "horizon_drift_log": hdrift,
        "horizon_vol_log": hvol,
        "drift_capped": capped,
    }


def _walk_forward_skill(closes: Sequence[float], steps: int, window: int, method: str,
                        max_folds: int = 60, min_folds: int = 3,
                        min_window: int = 40) -> Optional[Dict[str, object]]:
    """Out-of-sample forecast error vs. a random-walk baseline.

    At each anchor ``t`` fit the *same* model on the trailing window, project
    ``steps`` ahead, and compare its median to the realised close ``steps`` later.
    The naive baseline is the anchor close itself (a driftless random walk). Also
    tracks directional accuracy and 80%-interval coverage.

    The fitting window auto-shrinks to whatever the history can support so a
    short (e.g. ~100-bar) series still yields a measured — if noisy — skill number
    instead of silence. Returns ``None`` only when even a minimal window can't
    produce ``min_folds`` folds, i.e. the history is genuinely too short.
    """
    n = len(closes)
    # Largest fitting window that still leaves room for min_folds folds after the
    # warm-up: folds = n - steps - w - 1 >= min_folds  =>  w <= n - steps - min_folds - 1.
    max_feasible_w = n - steps - min_folds - 1
    if max_feasible_w < min_window:
        return None  # genuinely too little history to measure skill
    window = min(window, max_feasible_w)

    logret_full = [None] * n
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            logret_full[i] = math.log(closes[i] / closes[i - 1])

    first_anchor = window + 1
    last_anchor = n - 1 - steps
    if last_anchor < first_anchor:
        return None

    anchors = list(range(first_anchor, last_anchor + 1))
    if len(anchors) > max_folds:
        stride = len(anchors) / max_folds
        anchors = [anchors[int(k * stride)] for k in range(max_folds)]

    ae_model: List[float] = []
    ae_naive: List[float] = []
    dir_hits = 0
    covered = 0
    folds = 0
    for t in anchors:
        win = [r for r in logret_full[t - window + 1:t + 1] if r is not None]
        if len(win) < max(20, window // 2):
            continue
        anchor_close = closes[t]
        realised = closes[t + steps]
        if anchor_close <= 0 or realised <= 0:
            continue
        fit = _fit(win, method)
        proj = _project(anchor_close, fit["drift"], fit["sigma"], steps)
        model_pred = proj["median"]
        ae_model.append(abs(model_pred - realised))
        ae_naive.append(abs(anchor_close - realised))
        if (model_pred >= anchor_close) == (realised >= anchor_close):
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
        "method": method,
        "folds": folds,
        "mae_model": mae_model,
        "mae_naive": mae_naive,
        "skill_score": skill,
        "beats_random_walk": bool(skill is not None and skill > 0),
        "directional_accuracy": dir_hits / folds,
        "coverage_80": covered / folds,
        "nominal_coverage_80": 0.80,
        "noise_dominated": folds < 30,  # §21: under ~30 origins, stats are noisy
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
        The trailing close series (oldest first). Needs ≥ 60 bars.
    horizon_days:
        Forecast horizon in **calendar** days (default 30). Converted to trading
        days internally.
    method:
        ``"drift"`` (default), ``"naive"`` (random walk), or ``"blend"`` (drift +
        capped momentum). ``"zero_drift"`` / ``"random_walk"`` alias ``"naive"``.
    with_skill:
        Run the walk-forward skill check — what lets the report state measured
        error instead of asserting confidence.

    Returns a dict with ``median``, ``mean``, ``interval_80``, ``interval_95``,
    ``p_up``, ``expected_return``, ``mean_return``, the model ``inputs``, and (if
    requested) ``skill``. On insufficient/degenerate history it returns
    ``{"error": ...}`` rather than a guess.
    """
    method = _METHOD_ALIASES.get(method, method)
    closes = [float(c) for c in closes if c is not None]
    if len(closes) < _MIN_BARS:
        return {"error": f"forecast_price: history too short ({len(closes)} closes) — "
                         f"need at least {_MIN_BARS} bars to forecast"}
    ref = float(last_close) if last_close is not None else closes[-1]
    if ref <= 0:
        return {"error": "forecast_price: non-positive last close"}

    steps = horizon_trading_days(horizon_days, trading_ratio)
    eff_window = min(window, len(closes) - 1)
    win = _log_returns(closes[-(eff_window + 1):])
    if len(win) < 20:
        return {"error": "forecast_price: too few usable log returns"}

    fit = _fit(win, method)
    if fit["sigma"] <= 0:
        return {"error": "forecast_price: zero volatility (flat history) — no distribution to project"}

    proj = _project(ref, fit["drift"], fit["sigma"], steps)
    band_width_pct = (proj["interval_80"][1] - proj["interval_80"][0]) / ref
    result: Dict[str, object] = {
        "method": method,
        "horizon_days": horizon_days,
        "horizon_trading_days": steps,
        "last_close": ref,
        "median": proj["median"],
        "mean": proj["mean"],
        "interval_80": proj["interval_80"],
        "interval_95": proj["interval_95"],
        "p_up": proj["p_up"],
        "expected_return": proj["expected_return"],
        "mean_return": proj["mean_return"],
        "band_width_80_pct": band_width_pct,
        "uninformative": band_width_pct > 0.5,  # §21: flag near-useless horizons
        "drift_capped": proj["drift_capped"],
        "inputs": {
            "window_bars": eff_window,
            "daily_drift_log": fit["drift"],
            "daily_drift_raw_log": fit["drift_raw"],
            "drift_shrink_factor": fit["drift_shrink_factor"],
            "drift_t_stat": fit["drift_t_stat"],
            "daily_vol_log_ewma": fit["sigma"],
            "annualized_vol_pct": fit["sigma"] * math.sqrt(252) * 100.0,
            "vol_estimator": f"EWMA(lambda={_EWMA_LAMBDA})",
        },
        "skill": None,
        "in_sample": True,  # the distribution's own params are fit in-sample
    }
    if with_skill:
        result["skill"] = _walk_forward_skill(closes, steps, eff_window, method)
    return result


def compare_methods(closes: Sequence[float], horizon_days: int = 30,
                    methods: Sequence[str] = ("naive", "drift", "blend"),
                    window: int = 120) -> Dict[str, object]:
    """Score every method over identical walk-forward origins.

    Lets method choice be evidence-based: each method's forecast is evaluated on
    the same anchors, so the comparison is apples-to-apples. Returns per-method
    skill statistics and the best method by walk-forward skill (or ``naive`` when
    nothing beats the random walk).
    """
    closes = [float(c) for c in closes if c is not None]
    if len(closes) < _MIN_BARS:
        return {"error": f"compare_methods: need at least {_MIN_BARS} bars"}
    steps = horizon_trading_days(horizon_days)
    eff_window = min(window, len(closes) - 1)
    rows = []
    for m in methods:
        sk = _walk_forward_skill(closes, steps, eff_window, _METHOD_ALIASES.get(m, m))
        rows.append({"method": m, "skill": sk})
    scored = [r for r in rows if r["skill"] and r["skill"].get("skill_score") is not None]

    # No skill could be MEASURED (too little history) is not the same as skill
    # measured to be zero/negative. Never assert "no edge" from an absent measurement.
    if not scored:
        return {
            "horizon_days": horizon_days,
            "horizon_trading_days": steps,
            "methods": rows,
            "best_method": "naive",
            "skill_measured": False,
            "note": (f"insufficient history to measure out-of-sample skill "
                     f"({len(closes)} bars; a ~{eff_window + steps + 5}-bar minimum is "
                     f"needed for this horizon). Using naive as the safe default — the "
                     f"point forecast is unvalidated, so read the interval, not the point."),
        }

    best = max(scored, key=lambda r: r["skill"]["skill_score"])
    beats = best["skill"]["skill_score"] > 0
    noisy = any(r["skill"].get("noise_dominated") for r in scored)
    note = (f"best method ({best['method']}) beats a random walk out-of-sample "
            f"(skill {best['skill']['skill_score']:+.2f}, {best['skill']['folds']} folds)"
            if beats else
            "no method beats a random walk out-of-sample; use naive and read the interval")
    if noisy:
        note += (". Note: fewer than ~30 walk-forward folds — this comparison is "
                 "noise-dominated; treat the ranking as tentative, not decisive.")
    return {
        "horizon_days": horizon_days,
        "horizon_trading_days": steps,
        "methods": rows,
        "best_method": best["method"] if beats else "naive",
        "skill_measured": True,
        "noise_dominated": noisy,
        "note": note,
    }
