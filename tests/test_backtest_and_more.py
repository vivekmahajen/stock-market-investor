"""Tests for backtester, levels, seasonality, scoring, and the analysis envelope."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas import scoring
from atlas.analysis import analyze, build_signal, classify_regime, confluence_score
from atlas.backtest import run_backtest, verdict
from atlas.data import SyntheticProvider
from atlas.levels import detect_levels
from atlas.seasonality import compute_seasonality
from atlas.tools import ToolRegistry
from atlas.types import OHLCV, Bar


def _series(closes):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], closes[i] + 0.5, closes[i] - 0.5, closes[i], 1000.0)
        for i in range(len(closes))
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


def test_backtest_buy_and_hold_uptrend_profits():
    closes = [100 + i for i in range(200)]
    s = _series(closes)
    res = run_backtest(s, lambda ser, i: 1, commission_bps=0, slippage_bps=0)
    assert res.metrics["total_return_pct"] > 0
    assert res.equity_curve[-1] > res.equity_curve[0]


def test_backtest_costs_reduce_return():
    closes = [100 + i for i in range(200)]
    s = _series(closes)
    free = run_backtest(s, lambda ser, i: 1, commission_bps=0, slippage_bps=0)
    costly = run_backtest(s, lambda ser, i: 1, commission_bps=10, slippage_bps=10)
    assert costly.metrics["total_return_pct"] < free.metrics["total_return_pct"]


def test_backtest_small_sample_warns():
    closes = [100 + i for i in range(50)]
    s = _series(closes)
    res = run_backtest(s, lambda ser, i: 1)
    assert any("too small" in w for w in res.warnings)


def test_verdict_small_sample():
    assert "noise" in verdict({"sharpe": 3.0, "profit_factor": 5.0}, num_trades=5)


def test_no_lookahead_flat_signal_no_trades():
    s = _series([100 + i for i in range(100)])
    res = run_backtest(s, lambda ser, i: 0)
    assert len(res.trades) == 0


def test_detect_levels_finds_support_resistance():
    # Oscillating series creates repeated swing highs/lows.
    closes = [10, 12, 10, 12, 10, 12, 10, 12, 10, 12]
    s = _series(closes)
    lv = detect_levels(s, left=1, right=1, tolerance_pct=1.0)
    assert lv["support"] or lv["resistance"]
    assert lv["recent_high"] >= lv["recent_low"]


def test_seasonality_buckets_and_thin_warning():
    closes = [100 + i * 0.1 for i in range(400)]
    s = _series(closes)
    res = compute_seasonality(s, "month")
    assert res["buckets"]
    assert all("samples" in b for b in res["buckets"])


def test_seasonality_bad_granularity():
    s = _series([100, 101, 102])
    with pytest.raises(ValueError):
        compute_seasonality(s, "hourly")


def test_atlas_score_renormalises_missing_subscores():
    res = scoring.atlas_score({"technical": 80, "fundamental": None, "sentiment": None,
                               "relative_strength": None, "risk": 60})
    # Only technical (0.35) and risk (0.15) present -> weights renormalise to 0.7/0.3.
    expected = 80 * (0.35 / 0.5) + 60 * (0.15 / 0.5)
    assert res.score == pytest.approx(expected)
    assert res.label in {"buy", "accumulate", "hold", "reduce", "avoid"}


def test_atlas_score_no_subscores_raises():
    with pytest.raises(ValueError):
        scoring.atlas_score({"technical": None})


def test_classify_regime_uptrend():
    closes = [100 * (1.01 ** i) for i in range(120)]
    s = _series(closes)
    assert classify_regime(s) in {"trending_up", "high_vol"}


def test_confluence_score_range():
    closes = [100 + i for i in range(120)]
    s = _series(closes)
    c = confluence_score(s)
    assert c["score"] is None or 0 <= c["score"] <= 100


def test_analyze_envelope_has_required_fields():
    reg = ToolRegistry(SyntheticProvider(seed=7))
    out = analyze("AAPL", registry=reg, lookback=300)
    for key in ["symbol", "asof", "regime", "atlas_score", "subscores", "levels", "disclaimer"]:
        assert key in out
    # No fundamentals/sentiment feed -> those sub-scores must be None, not fabricated.
    assert out["subscores"]["fundamental"] is None
    assert out["subscores"]["sentiment"] is None
    # Synthetic data must be flagged.
    assert out["data_is_simulated"] is True


def test_build_signal_rejects_low_r():
    sig = build_signal("AAPL", entry=100, stop=95, targets=[102], direction="long",
                       account_equity=100_000)
    assert any("rejected" in w for w in sig["warnings"])


def test_build_signal_good_r_sizes_position():
    sig = build_signal("AAPL", entry=100, stop=95, targets=[110, 115], direction="long",
                       account_equity=100_000)
    assert sig["r_multiple"] == pytest.approx(2.0)
    assert sig["position_size"]["units"] == 200
