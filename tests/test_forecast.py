"""Tests for the horizon forecast engine (Section 21)."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from atlas.forecast import (DRIFT_CAP_SIGMA, METHODS, backtest_forecast,
                            bars_for_horizon, compare_methods, ewma_volatility,
                            forecast, log_returns, prob_above)
from atlas.types import OHLCV, Bar


def _series(closes, symbol="AAA", spread=0.01):
    """Build a series whose highs/lows bracket each close."""
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi, lo = max(o, c) * (1 + spread), min(o, c) * (1 - spread)
        bars.append(Bar(t0 + timedelta(days=i), o, hi, lo, c, 1e6))
    return OHLCV.from_bars(symbol, "1d", bars)


def _walk(n=400, start=100.0, drift=0.0004, vol=0.012, seed=3):
    """Deterministic pseudo-random geometric walk (no numpy, no RNG state)."""
    closes, price, x = [], start, seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        u1 = (x % 10_000 + 1) / 10_001
        x = (1103515245 * x + 12345) % (2 ** 31)
        u2 = (x % 10_000 + 1) / 10_001
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        price *= math.exp(drift + vol * z)
        closes.append(price)
    return closes


# --- helpers -------------------------------------------------------------
def test_bars_for_horizon_converts_calendar_to_trading_days():
    assert bars_for_horizon(30) == 21
    assert bars_for_horizon(365) == 252
    assert bars_for_horizon(1) == 1  # never zero


def test_log_returns_length_and_value():
    assert log_returns([100, 110]) == [pytest.approx(math.log(1.1))]
    assert len(log_returns([1, 2, 3, 4])) == 3


def test_log_returns_skips_non_positive_prices():
    assert log_returns([100, 0, 100]) == []


def test_ewma_volatility_needs_20_observations():
    assert ewma_volatility([0.01] * 19) is None
    assert ewma_volatility([0.01, -0.01] * 20) is not None


# --- the forecast envelope ----------------------------------------------
def test_forecast_refuses_short_history():
    out = forecast(_series(_walk(40)), 30)
    assert "error" in out and "60 bars" in out["error"]


def test_forecast_refuses_flat_series():
    out = forecast(_series([100.0] * 200), 30)
    assert "error" in out


def test_forecast_rejects_unknown_method():
    out = forecast(_series(_walk()), 30, method="crystal_ball")
    assert "error" in out and "unknown method" in out["error"]


def test_forecast_intervals_are_nested_and_ordered():
    f = forecast(_series(_walk()), 30)
    assert f["interval_95"]["low"] < f["interval_80"]["low"] < f["forecast_price"]
    assert f["forecast_price"] < f["interval_80"]["high"] < f["interval_95"]["high"]


def test_forecast_mean_exceeds_median_for_lognormal():
    f = forecast(_series(_walk()), 30)
    assert f["expected_price"] > f["forecast_price"]


def test_naive_method_forecasts_no_change():
    f = forecast(_series(_walk()), 30, method="naive")
    assert f["forecast_price"] == pytest.approx(f["last_close"], rel=1e-9)
    assert f["forecast_return_pct"] == pytest.approx(0.0, abs=1e-9)
    assert f["prob_up"] == pytest.approx(0.5, abs=1e-6)


def test_forecast_target_date_is_horizon_calendar_days_out():
    s = _series(_walk())
    f = forecast(s, 30)
    assert f["target_date"] == (s.asof + timedelta(days=30)).date().isoformat()
    assert f["horizon_bars"] == 21


def test_drift_is_shrunk_toward_zero():
    """A strong sample drift must not pass through at face value."""
    f = forecast(_series(_walk(drift=0.002)), 30)
    c = f["components"]
    assert 0.0 < c["shrinkage_applied"] < 1.0
    implied_annual = (math.exp(c["mu_horizon"] * 252 / c["bars_in_horizon"]) - 1) * 100
    assert abs(implied_annual) < abs(c["mu_raw_annual_pct"])


def test_horizon_drift_never_exceeds_the_sigma_cap():
    f = forecast(_series(_walk(drift=0.01, vol=0.004)), 30)
    c = f["components"]
    assert abs(c["mu_horizon"]) <= DRIFT_CAP_SIGMA * c["sigma_horizon"] + 1e-9
    assert any("capped" in w for w in f["warnings"])


def test_blend_adds_a_momentum_tilt_and_labels_it():
    up = _series(_walk(drift=0.0015, vol=0.008))
    d = forecast(up, 30, method="drift")
    b = forecast(up, 30, method="blend")
    assert b["components"]["momentum_63b_pct"] is not None
    assert "momentum" in b["components"]["drift_source"]
    assert b["forecast_price"] != d["forecast_price"]


def test_prob_up_reflects_drift_sign():
    assert forecast(_series(_walk(drift=0.0015)), 30)["prob_up"] > 0.5
    assert forecast(_series(_walk(drift=-0.0015)), 30)["prob_up"] < 0.5


def test_short_sample_produces_a_warning():
    f = forecast(_series(_walk(90)), 30)
    assert any("return observations" in w for w in f["warnings"])


def test_forecast_never_promises():
    f = forecast(_series(_walk()), 30)
    text = " ".join([f["disclaimer"], f["forecast_price_basis"]]).lower()
    for banned in ("will reach", "guarantee", "target price of"):
        assert banned not in text


# --- prob_above ----------------------------------------------------------
def test_prob_above_is_monotonically_decreasing_in_level():
    f = forecast(_series(_walk()), 30)
    last = f["last_close"]
    assert prob_above(f, last * 0.9) > prob_above(f, last) > prob_above(f, last * 1.1)


def test_prob_above_at_median_is_one_half():
    f = forecast(_series(_walk()), 30)
    assert prob_above(f, f["forecast_price"]) == pytest.approx(0.5, abs=1e-3)


def test_prob_above_rejects_nonsense_levels():
    assert prob_above(forecast(_series(_walk()), 30), 0) is None


# --- skill measurement ---------------------------------------------------
def test_backtest_reports_zero_samples_on_short_history():
    out = backtest_forecast(_series(_walk(100)), 30)
    assert out["samples"] == 0 and "not enough history" in out["note"]


def test_backtest_produces_the_full_metric_set():
    out = backtest_forecast(_series(_walk(600)), 30)
    for key in ("mae", "rmse", "mape_pct", "naive_mape_pct", "skill_vs_naive",
                "directional_accuracy_pct", "coverage_80_pct", "coverage_95_pct", "verdict"):
        assert key in out
    assert out["samples"] > 30


def test_backtest_coverage_is_a_percentage():
    out = backtest_forecast(_series(_walk(600)), 30)
    assert 0 <= out["coverage_80_pct"] <= 100
    assert out["coverage_95_pct"] >= out["coverage_80_pct"]


def test_naive_baseline_has_exactly_zero_skill_against_itself():
    out = backtest_forecast(_series(_walk(600)), 30, method="naive")
    assert out["skill_vs_naive"] == pytest.approx(0.0, abs=1e-9)


def test_small_sample_backtest_is_flagged_as_noise():
    out = backtest_forecast(_series(_walk(200)), 30, min_train=120, step=20)
    assert out["samples"] < 30
    assert "too small" in out["verdict"].lower()
    assert any("noise-dominated" in w for w in out["warnings"])


def test_negative_skill_is_reported_plainly():
    """A verdict must never dress up a model that loses to a random walk."""
    out = dict(samples=100, skill_vs_naive=-0.08, directional_accuracy_pct=48.0)
    from atlas.forecast import _skill_verdict
    assert "no measurable edge" in _skill_verdict(out).lower()


def test_compare_methods_scores_every_method_over_the_same_origins():
    out = compare_methods(_series(_walk(600)), 30)
    assert set(out["results"]) == set(METHODS)
    counts = {out["results"][m]["samples"] for m in METHODS}
    assert len(counts) == 1  # identical origins -> identical sample counts
    assert out["ranked_by_mape"][0] in METHODS
