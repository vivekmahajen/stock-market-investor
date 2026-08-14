"""Tests for portfolio optimization, screening, alerts, and calibration."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.alerts import AlertStore
from atlas.calibration import CalibrationLog, LOSS, WIN
from atlas.data import SyntheticProvider
from atlas.portfolio import correlation_matrix, covariance_matrix, daily_returns, optimize_portfolio
from atlas.screen import run_screen, symbol_metrics
from atlas.tools import ToolRegistry
from atlas.types import OHLCV, Bar


def _series(closes, symbol="TEST"):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], closes[i] + 0.5, closes[i] - 0.5, closes[i], 1_000_000.0)
        for i in range(len(closes))
    ]
    return OHLCV.from_bars(symbol, "1d", bars)


# --- portfolio -----------------------------------------------------------
def _provider_series(n=200):
    p = SyntheticProvider(seed=3)
    return {
        "AAA": p.get_ohlcv("AAA", "1d", n),
        "BBB": p.get_ohlcv("BBB", "1d", n),
        "CCC": p.get_ohlcv("CCC", "1d", n),
    }


def test_covariance_symmetric():
    rets = [daily_returns(s) for s in _provider_series().values()]
    n = min(len(r) for r in rets)
    rets = [r[-n:] for r in rets]
    cov = covariance_matrix(rets)
    for i in range(len(cov)):
        for j in range(len(cov)):
            assert cov[i][j] == pytest.approx(cov[j][i])


def test_correlation_diag_is_one():
    rets = [daily_returns(s) for s in _provider_series().values()]
    n = min(len(r) for r in rets)
    rets = [r[-n:] for r in rets]
    corr = correlation_matrix(covariance_matrix(rets))
    for i in range(len(corr)):
        assert corr[i][i] == pytest.approx(1.0, abs=1e-9)


def test_weights_sum_to_one_all_objectives():
    series = _provider_series()
    for obj in ["equal_weight", "inverse_vol", "min_variance", "max_sharpe"]:
        res = optimize_portfolio(series, objective=obj)
        assert sum(res.weights.values()) == pytest.approx(1.0, abs=1e-6)
        assert all(w >= -1e-9 for w in res.weights.values())  # long-only


def test_min_variance_not_higher_vol_than_equal_weight():
    series = _provider_series()
    mv = optimize_portfolio(series, objective="min_variance")
    ew = optimize_portfolio(series, objective="equal_weight")
    assert mv.expected_vol <= ew.expected_vol + 1e-6


def test_max_weight_cap_respected():
    series = _provider_series()
    res = optimize_portfolio(series, objective="max_sharpe", max_weight=0.4)
    assert max(res.weights.values()) <= 0.4 + 1e-6


def test_portfolio_requires_two_symbols():
    p = SyntheticProvider(seed=1)
    with pytest.raises(ValueError):
        optimize_portfolio({"AAA": p.get_ohlcv("AAA", "1d", 100)})


def test_portfolio_benchmark_beta_and_stress():
    p = SyntheticProvider(seed=5)
    series = _provider_series()
    bench = p.get_ohlcv("SPY", "1d", 200)
    res = optimize_portfolio(series, objective="equal_weight", benchmark=bench)
    assert res.diagnostics["beta_vs_benchmark"] is not None
    assert res.diagnostics["stress_test"]["scenario"] == "market -10%"


# --- screen --------------------------------------------------------------
def test_symbol_metrics_fields():
    s = _series([100 + i * 0.5 for i in range(120)])
    m = symbol_metrics(s)
    assert m["above_ema50"] is True
    assert m["price"] > 0


def test_symbol_metrics_short_series_none():
    assert symbol_metrics(_series([100, 101, 102])) is None


def test_run_screen_filters_and_ranks():
    prov = SyntheticProvider(seed=11)
    res = run_screen(["AAA", "BBB", "CCC", "DDD"], filters=[{"field": "above_ema50", "op": "==", "value": True}],
                     provider=prov, limit=10)
    assert res["simulated"] is True
    # results are sorted by composite score descending
    scores = [r["composite_score"] for r in res["results"]]
    assert scores == sorted(scores, reverse=True)
    for r in res["results"]:
        assert r["metrics"]["above_ema50"] is True


def test_run_screen_liquidity_flags():
    s = _series([3.0 + i * 0.001 for i in range(120)])  # low price
    m = symbol_metrics(s)
    from atlas.screen import liquidity_flags
    flags = liquidity_flags(m, min_adv=1e12, min_price=5.0)
    assert any("low price" in f for f in flags)
    assert any("thin liquidity" in f for f in flags)


# --- alerts --------------------------------------------------------------
def test_alert_price_above_triggers():
    store = AlertStore()
    a = store.create_alert("AAA", {"kind": "price_above", "value": 50})
    s = _series([100 + i for i in range(60)])
    res = store.evaluate(a, s)
    assert res["triggered"] is True
    assert "not a trade" in res["reminder"]
    # Alert deactivates after firing.
    assert a.active is False


def test_alert_unknown_kind_rejected():
    store = AlertStore()
    with pytest.raises(ValueError):
        store.create_alert("AAA", {"kind": "moon_phase", "value": 1})


def test_alert_atr_move_dynamic():
    store = AlertStore()
    a = store.create_alert("AAA", {"kind": "atr_move", "mult": 0.1, "period": 14})
    s = _series([100 + i for i in range(60)])
    res = store.evaluate(a, s)
    assert "detail" in res


def test_alert_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "alerts.json")
    store = AlertStore(path)
    store.create_alert("AAA", {"kind": "price_below", "value": 10})
    reloaded = AlertStore(path)
    assert len(reloaded.list_alerts()) == 1


# --- calibration ---------------------------------------------------------
def test_calibration_perfect_is_well_calibrated():
    log = CalibrationLog()
    # 10 signals at 90% confidence, 9 win -> realized 90%.
    for i in range(10):
        log.log_signal("AAA", "long", 90.0, created="2026-01-01", signal_id=f"s{i}")
    for i in range(9):
        log.resolve(f"s{i}", WIN, realized_r=2.0)
    log.resolve("s9", LOSS, realized_r=-1.0)
    m = log.metrics(buckets=5)
    assert m["resolved"] == 10
    assert m["win_rate_pct"] == 90.0
    # 90% predicted vs 90% realized -> low ECE.
    assert m["expected_calibration_error"] < 0.05


def test_calibration_empty():
    assert CalibrationLog().metrics()["resolved"] == 0


def test_calibration_confidence_bounds():
    log = CalibrationLog()
    with pytest.raises(ValueError):
        log.log_signal("AAA", "long", 150.0, created="2026-01-01")


def test_calibration_persistence(tmp_path):
    path = str(tmp_path / "cal.json")
    log = CalibrationLog(path)
    log.log_signal("AAA", "long", 70.0, created="2026-01-01", signal_id="x1")
    log.resolve("x1", WIN, realized_r=1.5)
    reloaded = CalibrationLog(path)
    assert reloaded.metrics()["resolved"] == 1


# --- registry wiring -----------------------------------------------------
def test_registry_screen_and_portfolio_and_alert():
    reg = ToolRegistry(SyntheticProvider(seed=9))
    screen = reg.run_screen(["AAA", "BBB", "CCC"], limit=5)
    assert "results" in screen
    port = reg.optimize_portfolio(["AAA", "BBB", "CCC"], objective="min_variance")
    assert "weights" in port
    alert = reg.create_alert("AAA", {"kind": "price_above", "value": 1})
    assert alert["symbol"] == "AAA"
