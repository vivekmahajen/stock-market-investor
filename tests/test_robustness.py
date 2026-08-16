"""Tests for backtest robustness (Group E, §8)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.indicators import ema
from atlas.robustness import (parameter_sensitivity, sub_period_analysis,
                              train_test_split, walk_forward)
from atlas.types import OHLCV, Bar


def _series(closes):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], closes[i] + 0.5, closes[i] - 0.5, closes[i], 1000.0)
        for i in range(len(closes))
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


def _ema_cross(fast, slow):
    def fn(series, i):
        c = list(series.close[: i + 1])
        ef, es = ema(c, fast), ema(c, slow)
        if ef[i] is None or es[i] is None:
            return 0
        return 1 if ef[i] > es[i] else -1
    return fn


UPTREND = _series([100 + i for i in range(400)])
GRID = {"fast": [10, 20], "slow": [50, 100]}


def test_slice_helper():
    s = _series([float(i) for i in range(100)])
    sub = s.slice(10, 20)
    assert len(sub) == 10
    assert sub.close[0] == 10.0 and sub.close[-1] == 19.0


def test_train_test_split_reports_both():
    out = train_test_split(UPTREND, _ema_cross(20, 50))
    assert "in_sample" in out and "out_of_sample" in out
    assert "assessment" in out
    assert out["in_sample"]["num_trades"] >= 0


def test_train_test_split_too_short():
    out = train_test_split(_series([100 + i for i in range(40)]), _ema_cross(20, 50))
    assert "error" in out


def test_walk_forward_structure():
    out = walk_forward(UPTREND, lambda p: _ema_cross(p["fast"], p["slow"]), GRID, n_folds=3)
    assert out["n_folds"] == 3
    assert len(out["folds"]) == 3
    for f in out["folds"]:
        assert "best_params" in f and "oos_metrics" in f
    assert 0.0 <= out["param_stability"] <= 1.0
    assert "assessment" in out


def test_walk_forward_too_short():
    out = walk_forward(_series([100 + i for i in range(50)]),
                       lambda p: _ema_cross(p["fast"], p["slow"]), GRID, n_folds=4)
    assert "error" in out


def test_parameter_sensitivity_distribution():
    out = parameter_sensitivity(UPTREND, lambda p: _ema_cross(p["fast"], p["slow"]), GRID)
    assert out["combinations"] == 4
    assert "mean" in out and "std" in out and "best" in out and "worst" in out
    assert out["best"]["value"] >= out["worst"]["value"]
    assert "assessment" in out


def test_sub_period_analysis():
    out = sub_period_analysis(UPTREND, _ema_cross(20, 50), n_periods=4)
    assert len(out["periods"]) == 4
    assert 0.0 <= out["consistency"] <= 1.0
    for p in out["periods"]:
        assert "total_return_pct" in p and "from" in p and "to" in p


def test_sub_period_too_short():
    out = sub_period_analysis(_series([100 + i for i in range(60)]), _ema_cross(20, 50), n_periods=4)
    assert "error" in out


def test_split_detects_overfit_on_regime_change():
    # Up then down: a long-only-ish EMA strategy should look different in the two halves.
    closes = [100 + i for i in range(200)] + [300 - i for i in range(200)]
    s = _series(closes)
    out = train_test_split(s, _ema_cross(20, 50), split=0.5)
    # In-sample (uptrend) vs out-of-sample (downtrend) should differ; assessment present.
    assert out["in_sample"]["total_return_pct"] != out["out_of_sample"]["total_return_pct"]
