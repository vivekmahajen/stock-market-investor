"""Horizon price forecasting — the 30-day projection behind the daily report.

A forecast here is **not** a prediction of what the price will be. It is a
probability distribution over where the price could be in ``horizon_days``,
derived only from the series handed to it, stated with an interval, and — this
is the part that matters — **measured against a random walk** so the user knows
whether the model has any skill at all on this symbol.

The model
---------
Geometric random walk with a *shrunk* drift:

1. Daily log returns ``r_t = ln(C_t / C_{t-1})`` over ``lookback`` bars.
2. Volatility ``sigma`` from an EWMA of squared returns (lambda 0.94) — more
   responsive than a flat sample standard deviation to the current regime.
3. Raw drift ``mu_hat = mean(r)``. A sample mean over a year of daily returns is
   mostly noise, so it is shrunk toward zero against a stated prior:
   ``shrinkage = tau^2 / (tau^2 + se^2)`` where ``se = sigma / sqrt(n)`` and
   ``tau`` is the prior standard deviation of daily drift
   (``PRIOR_ANNUAL_DRIFT_SD`` annualised). When the sample mean is small relative
   to its own standard error the drift collapses to ~0 — which is the honest
   answer.
4. The horizon drift is then hard-capped at ``DRIFT_CAP_SIGMA`` horizon standard
   deviations, so the point forecast can never be dominated by an extrapolated
   trend.
5. Horizon distribution: ``ln(S_T / S_0) ~ N(mu_h, sigma_h^2)`` with
   ``mu_h = mu * bars`` and ``sigma_h = sigma * sqrt(bars)``. The headline
   forecast is the **median**, ``S_0 * exp(mu_h)``; the lognormal mean is
   reported separately because they are not the same number.

Methods
-------
``naive``   — ``mu = 0``. A pure random walk: today's close *is* the forecast.
              This is the baseline every other method has to beat.
``drift``   — the shrunk-drift model above (default).
``blend``   — ``drift`` plus a small, capped momentum tilt from the 63-bar return.

Skill measurement
-----------------
:func:`backtest_forecast` walks the same model forward through history and
reports MAE / RMSE / MAPE, directional hit rate, and how often the realised
price actually landed inside the stated 80% and 95% bands (interval coverage —
a band that only covers 50% of outcomes is a lie, and this catches it). It also
runs the naive baseline over the identical origins and reports
``skill_vs_naive``. Negative skill is reported plainly, not buried.
"""
from __future__ import annotations

import math
from datetime import timedelta
from statistics import fmean, pstdev
from typing import Dict, List, Optional, Sequence

from .types import OHLCV

TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.0

#: Prior standard deviation of *annualised* drift, used to shrink the sample mean.
PRIOR_ANNUAL_DRIFT_SD = 0.15
#: The horizon drift may never exceed this many horizon standard deviations.
DRIFT_CAP_SIGMA = 1.0
#: EWMA decay for the volatility estimate (RiskMetrics convention).
EWMA_LAMBDA = 0.94
#: Weight on the momentum tilt in the ``blend`` method, and its cap in sigmas.
MOMENTUM_WEIGHT = 0.30
MOMENTUM_CAP_SIGMA = 0.5

MODEL_VERSION = "forecast-1.0"
METHODS = ("naive", "drift", "blend")

Z_80 = 1.2815515655446004
Z_95 = 1.959963984540054


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bars_for_horizon(horizon_days: int) -> int:
    """Trading bars in ``horizon_days`` *calendar* days (30d -> 21 bars)."""
    return max(1, int(round(horizon_days * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR)))


def log_returns(close: Sequence[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(close)):
        prev, cur = close[i - 1], close[i]
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def ewma_volatility(returns: Sequence[float], lam: float = EWMA_LAMBDA) -> Optional[float]:
    """Exponentially-weighted daily volatility. ``None`` below 20 observations."""
    if len(returns) < 20:
        return None
    var = pstdev(returns[: min(len(returns), 20)]) ** 2
    for r in returns:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var) if var > 0 else None


def _shrunk_drift(returns: Sequence[float], sigma: float) -> Dict[str, float]:
    """Sample drift shrunk toward zero against the ``PRIOR_ANNUAL_DRIFT_SD`` prior."""
    n = len(returns)
    mu_hat = fmean(returns)
    se = sigma / math.sqrt(n) if n > 0 else float("inf")
    # A *drift* scales linearly with time (mu_annual = 252 * mu_daily), so the
    # prior converts by dividing by the number of days — not by its square root,
    # which is the conversion for a volatility.
    tau = PRIOR_ANNUAL_DRIFT_SD / TRADING_DAYS_PER_YEAR
    shrinkage = (tau * tau) / (tau * tau + se * se) if (tau or se) else 0.0
    return {
        "mu_raw_daily": mu_hat,
        "mu_shrunk_daily": shrinkage * mu_hat,
        "std_error_daily": se,
        "shrinkage": shrinkage,
        "prior_annual_drift_sd": PRIOR_ANNUAL_DRIFT_SD,
    }


def _momentum_tilt(close: Sequence[float], bars: int, sigma_h: float) -> Dict[str, float]:
    """Capped momentum tilt on the horizon log-return, from the 63-bar return."""
    if len(close) < 64 or close[-64] <= 0:
        return {"momentum_63b_pct": None, "tilt_h": 0.0}
    ret63 = math.log(close[-1] / close[-64])
    tilt = MOMENTUM_WEIGHT * ret63 * (bars / 63.0)
    cap = MOMENTUM_CAP_SIGMA * sigma_h
    tilt = max(-cap, min(cap, tilt))
    return {"momentum_63b_pct": round((math.exp(ret63) - 1) * 100, 3), "tilt_h": tilt}


def forecast(
    series: OHLCV,
    horizon_days: int = 30,
    method: str = "drift",
    lookback: int = 252,
    model_version: str = MODEL_VERSION,
) -> dict:
    """Project ``series`` forward ``horizon_days`` calendar days.

    Returns the Section 21 forecast envelope: a median point forecast, 80% and
    95% intervals, the probability of finishing above today's close, every model
    input that produced them, and any warnings. Returns ``{"error": ...}`` rather
    than a number when the history is too short to estimate volatility — the
    forecast is refused, not faked.
    """
    if method not in METHODS:
        return {"error": f"unknown method '{method}'; choose from {', '.join(METHODS)}"}
    close = [c for c in series.close]
    if len(close) < 60:
        return {
            "error": f"need at least 60 bars to estimate a distribution; got {len(close)}",
            "symbol": series.symbol,
        }

    window = close[-lookback:] if lookback and len(close) > lookback else close
    rets = log_returns(window)
    sigma = ewma_volatility(rets)
    if sigma is None or sigma <= 0:
        return {
            "error": "volatility could not be estimated from this history (flat or degenerate series)",
            "symbol": series.symbol,
        }

    bars = bars_for_horizon(horizon_days)
    sigma_h = sigma * math.sqrt(bars)
    last_close = float(close[-1])

    drift = _shrunk_drift(rets, sigma)
    components: Dict[str, object] = {
        "sigma_daily": round(sigma, 6),
        "sigma_annual_pct": round(sigma * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2),
        "sigma_horizon": round(sigma_h, 6),
        "bars_in_horizon": bars,
        "sample_bars": len(rets),
        "mu_raw_annual_pct": round((math.exp(drift["mu_raw_daily"] * TRADING_DAYS_PER_YEAR) - 1) * 100, 2),
        "shrinkage_applied": round(drift["shrinkage"], 4),
    }
    warnings: List[str] = []

    if method == "naive":
        mu_h = 0.0
        components["drift_source"] = "none (random walk baseline)"
    else:
        mu_h = drift["mu_shrunk_daily"] * bars
        components["drift_source"] = "shrunk sample mean of daily log returns"
        if method == "blend":
            mom = _momentum_tilt(close, bars, sigma_h)
            mu_h += mom["tilt_h"]
            components["momentum_63b_pct"] = mom["momentum_63b_pct"]
            components["momentum_tilt_h"] = round(mom["tilt_h"], 6)
            components["drift_source"] = "shrunk sample mean + capped 63-bar momentum tilt"

    cap = DRIFT_CAP_SIGMA * sigma_h
    if abs(mu_h) > cap:
        warnings.append(
            f"horizon drift was capped at {DRIFT_CAP_SIGMA}x horizon sigma "
            f"({mu_h:+.4f} -> {math.copysign(cap, mu_h):+.4f}); the trend estimate was "
            f"large relative to the noise and is not extrapolated at face value."
        )
        mu_h = math.copysign(cap, mu_h)
    components["mu_horizon"] = round(mu_h, 6)

    median = last_close * math.exp(mu_h)
    mean = last_close * math.exp(mu_h + 0.5 * sigma_h * sigma_h)
    lo80, hi80 = last_close * math.exp(mu_h - Z_80 * sigma_h), last_close * math.exp(mu_h + Z_80 * sigma_h)
    lo95, hi95 = last_close * math.exp(mu_h - Z_95 * sigma_h), last_close * math.exp(mu_h + Z_95 * sigma_h)
    prob_up = _norm_cdf(mu_h / sigma_h) if sigma_h > 0 else None

    asof = series.asof
    target_date = (asof + timedelta(days=horizon_days)).date().isoformat() if asof else None

    if len(rets) < 120:
        warnings.append(
            f"only {len(rets)} return observations — volatility and drift estimates are noisy; "
            f"widen the lookback or treat the interval as a floor on the true uncertainty."
        )
    band_pct = (hi80 - lo80) / last_close * 100
    if band_pct > 40:
        warnings.append(
            f"the 80% band spans {band_pct:.0f}% of the current price — this symbol's "
            f"{horizon_days}-day outcome is close to uninformative at this volatility."
        )

    return {
        "symbol": series.symbol,
        "asof": asof.isoformat() if asof else None,
        "timeframe": series.timeframe,
        "horizon_days": horizon_days,
        "horizon_bars": bars,
        "target_date": target_date,
        "method": method,
        "model_version": model_version,
        "last_close": round(last_close, 4),
        "forecast_price": round(median, 4),
        "forecast_price_basis": "median of the lognormal horizon distribution",
        "expected_price": round(mean, 4),
        "expected_price_basis": "mean of the same distribution (above the median by sigma^2/2)",
        "forecast_return_pct": round((median / last_close - 1) * 100, 3),
        "interval_80": {"low": round(lo80, 4), "high": round(hi80, 4)},
        "interval_95": {"low": round(lo95, 4), "high": round(hi95, 4)},
        "interval_80_width_pct": round(band_pct, 2),
        "prob_up": round(prob_up, 4) if prob_up is not None else None,
        "components": components,
        "warnings": warnings,
        "disclaimer": (
            "A distribution, not a prediction. The point forecast is the median of a "
            "random walk with a shrunk drift — it is a reference level, not a target price."
        ),
    }


def prob_above(fc: dict, level: float) -> Optional[float]:
    """Probability the horizon price finishes above ``level``, from a forecast dict."""
    comp = fc.get("components") or {}
    sigma_h, mu_h = comp.get("sigma_horizon"), comp.get("mu_horizon")
    last = fc.get("last_close")
    if not sigma_h or last is None or mu_h is None or level <= 0 or last <= 0:
        return None
    z = (math.log(level / last) - mu_h) / sigma_h
    return round(1.0 - _norm_cdf(z), 4)


# --------------------------------------------------------------------------- #
# Skill measurement (does this model beat a coin flip and a random walk?)
# --------------------------------------------------------------------------- #
def backtest_forecast(
    series: OHLCV,
    horizon_days: int = 30,
    method: str = "drift",
    lookback: int = 252,
    min_train: int = 120,
    step: int = 5,
) -> dict:
    """Walk the forecast model forward through history and score it.

    At each origin the model sees only bars up to that point, forecasts
    ``horizon_days`` ahead, and is compared with the realised close. The naive
    random walk is scored over the *same* origins so ``skill_vs_naive`` is a
    like-for-like comparison.
    """
    bars = bars_for_horizon(horizon_days)
    n = len(series)
    origins = list(range(min_train, n - bars, max(1, step)))
    if not origins:
        return {
            "samples": 0,
            "note": (f"not enough history to score this model: need more than "
                     f"{min_train + bars} bars, have {n}."),
            "horizon_days": horizon_days,
            "method": method,
        }

    abs_err: List[float] = []
    sq_err: List[float] = []
    pct_err: List[float] = []
    naive_pct_err: List[float] = []
    dir_hits = 0
    dir_total = 0
    in80 = in95 = 0
    scored = 0

    for i in origins:
        train = series.slice(0, i + 1)
        fc = forecast(train, horizon_days=horizon_days, method=method, lookback=lookback)
        if "error" in fc:
            continue
        actual = float(series.close[i + bars])
        if actual <= 0:
            continue
        pred = fc["forecast_price"]
        last = fc["last_close"]
        scored += 1
        abs_err.append(abs(pred - actual))
        sq_err.append((pred - actual) ** 2)
        pct_err.append(abs(pred - actual) / actual * 100)
        naive_pct_err.append(abs(last - actual) / actual * 100)
        if actual != last:
            dir_total += 1
            if (pred >= last) == (actual >= last):
                dir_hits += 1
        if fc["interval_80"]["low"] <= actual <= fc["interval_80"]["high"]:
            in80 += 1
        if fc["interval_95"]["low"] <= actual <= fc["interval_95"]["high"]:
            in95 += 1

    if scored == 0:
        return {"samples": 0, "note": "no origin produced a usable forecast.",
                "horizon_days": horizon_days, "method": method}

    mape = fmean(pct_err)
    naive_mape = fmean(naive_pct_err)
    skill = (1.0 - mape / naive_mape) if naive_mape > 0 else None

    out = {
        "method": method,
        "model_version": MODEL_VERSION,
        "horizon_days": horizon_days,
        "samples": scored,
        "origins_step_bars": step,
        "mae": round(fmean(abs_err), 4),
        "rmse": round(math.sqrt(fmean(sq_err)), 4),
        "mape_pct": round(mape, 3),
        "naive_mape_pct": round(naive_mape, 3),
        "skill_vs_naive": round(skill, 4) if skill is not None else None,
        "directional_accuracy_pct": round(dir_hits / dir_total * 100, 2) if dir_total else None,
        "directional_samples": dir_total,
        "coverage_80_pct": round(in80 / scored * 100, 2),
        "coverage_95_pct": round(in95 / scored * 100, 2),
        "warnings": [],
    }
    out["verdict"] = _skill_verdict(out)
    if scored < 30:
        out["warnings"].append(
            f"only {scored} non-independent origins — these error statistics are noise-dominated. "
            f"Overlapping {horizon_days}-day windows also correlate, so the effective sample is smaller still."
        )
    if out["coverage_80_pct"] < 70:
        out["warnings"].append(
            f"the 80% band only contained {out['coverage_80_pct']}% of outcomes — the model "
            f"understates uncertainty on this symbol; widen the interval before relying on it."
        )
    if out["coverage_95_pct"] < 88:
        out["warnings"].append(
            f"the 95% band only contained {out['coverage_95_pct']}% of outcomes — tail risk is "
            f"underestimated (fat tails / regime shifts the lognormal does not model)."
        )
    return out


def _skill_verdict(m: dict) -> str:
    skill, n = m.get("skill_vs_naive"), m.get("samples", 0)
    d = m.get("directional_accuracy_pct")
    if n < 30:
        return ("Sample too small to judge — treat this forecast as a reference level with an "
                "uncertainty band, not as a model with demonstrated skill.")
    if skill is None:
        return "Skill could not be computed against the naive baseline."
    if skill <= 0:
        return (f"No measurable edge: the model's error is {abs(skill)*100:.1f}% worse than simply "
                f"assuming the price does not move. Use the interval; ignore the point forecast.")
    if skill < 0.02:
        return (f"Marginal: {skill*100:.1f}% better than a random walk — inside the noise. "
                f"The interval is the useful output here, not the point.")
    if d is not None and d < 52:
        return (f"Error is {skill*100:.1f}% below the random walk, but direction is right only "
                f"{d}% of the time — the model is calibrated on magnitude, not on direction.")
    return (f"Modest but measurable: {skill*100:.1f}% lower error than a random walk over "
            f"{n} origins, direction right {d}% of the time. Still a distribution, not a call.")


def compare_methods(series: OHLCV, horizon_days: int = 30, **kwargs) -> dict:
    """Score every method over the same origins so the caller can see the ranking."""
    results = {m: backtest_forecast(series, horizon_days=horizon_days, method=m, **kwargs)
               for m in METHODS}
    ranked = sorted(
        (m for m in METHODS if results[m].get("mape_pct") is not None),
        key=lambda m: results[m]["mape_pct"],
    )
    return {"symbol": series.symbol, "horizon_days": horizon_days,
            "results": results, "ranked_by_mape": ranked}
