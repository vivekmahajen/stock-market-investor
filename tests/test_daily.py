"""Tests for the ATLAS Daily Report subsystem.

Covers the forecast engine (distribution + walk-forward skill), the universe
resolver (resolved not recalled, with fallback), the prediction store
(log → resolve → aggregate), and the daily orchestrator + renderers.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import pytest

from atlas.daily import (forecast_accuracy, render_report, report_from_store,
                         resolve_predictions, run_daily_report)
from atlas.forecast import forecast_price, horizon_trading_days
from atlas.predictions import PredictionStore, accuracy_stats, target_date
from atlas.tools import ToolRegistry
from atlas.types import OHLCV, Bar
from atlas.universe import get_universe, list_universes


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _synthetic_closes(n=400, mu=0.0004, sigma=0.012, start=100.0, seed=7):
    """Deterministic lognormal walk (no dependency on random module)."""
    closes = [start]
    state = seed
    for _ in range(n - 1):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        u1 = (state / 0x7FFFFFFF) or 1e-9
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        u2 = state / 0x7FFFFFFF
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        closes.append(closes[-1] * math.exp(mu + sigma * z))
    return closes


class DatedProvider:
    """Minimal provider returning a fixed dated OHLCV for any symbol."""

    simulated = False
    source = "test"

    def __init__(self, closes, start_date="2026-01-01"):
        self._closes = closes
        self._start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)

    def get_ohlcv(self, symbol, timeframe, lookback):
        bars = []
        for i, c in enumerate(self._closes):
            ts = self._start + timedelta(days=i)
            bars.append(Bar(ts=ts, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1e6))
        series = OHLCV.from_bars(symbol, timeframe, bars)
        return series.tail(lookback) if lookback else series

    def get_quote(self, symbol):  # pragma: no cover - unused here
        raise NotImplementedError

    def provenance(self, tool, symbol, timeframe, lookback):
        from atlas.types import Provenance
        return Provenance(tool=tool, symbol=symbol, timeframe=timeframe,
                          lookback=lookback, source=self.source, simulated=False)


# --------------------------------------------------------------------------- #
# forecast_price
# --------------------------------------------------------------------------- #
def test_forecast_price_shape():
    closes = _synthetic_closes(300)
    fc = forecast_price(closes, horizon_days=30, method="drift", with_skill=True)
    assert "error" not in fc
    lo80, hi80 = fc["interval_80"]
    lo95, hi95 = fc["interval_95"]
    # Intervals bracket the median and 95% is wider than 80%.
    assert lo80 < fc["median"] < hi80
    assert lo95 <= lo80 and hi95 >= hi80
    assert 0.0 <= fc["p_up"] <= 1.0
    assert fc["horizon_trading_days"] == horizon_trading_days(30)


def test_forecast_price_refuses_short_history():
    fc = forecast_price([100.0, 101.0, 102.0], horizon_days=30)
    assert "error" in fc


def test_forecast_price_zero_drift_has_pup_half():
    closes = _synthetic_closes(300)
    fc = forecast_price(closes, horizon_days=30, method="zero_drift", with_skill=False)
    assert abs(fc["p_up"] - 0.5) < 1e-9  # no drift => symmetric


def test_forecast_skill_reports_baseline():
    closes = _synthetic_closes(400)
    fc = forecast_price(closes, horizon_days=30, method="drift", with_skill=True)
    sk = fc["skill"]
    assert sk is not None
    assert sk["folds"] >= 3
    assert "skill_score" in sk and "beats_random_walk" in sk
    assert 0.0 <= sk["directional_accuracy"] <= 1.0
    assert sk["in_sample"] is False  # skill is out-of-sample by construction


def test_forecast_flat_history_rejected():
    fc = forecast_price([100.0] * 200, horizon_days=30)
    assert "error" in fc  # zero volatility => no distribution


def test_forecast_refuses_under_60_bars():
    fc = forecast_price(_synthetic_closes(50), horizon_days=30)
    assert "error" in fc and "60" in fc["error"]
    assert "error" not in forecast_price(_synthetic_closes(80), horizon_days=30, with_skill=False)


def test_forecast_mean_exceeds_median():
    # Lognormal: the mean sits above the median. They must be reported distinctly.
    fc = forecast_price(_synthetic_closes(300), horizon_days=30, with_skill=False)
    assert fc["mean"] > fc["median"]
    assert fc["mean_return"] > fc["expected_return"]


def test_forecast_drift_is_shrunk_and_capped():
    fc = forecast_price(_synthetic_closes(300), horizon_days=30, with_skill=False)
    inp = fc["inputs"]
    # Shrinkage pulls the used drift toward zero relative to the raw sample mean.
    assert abs(inp["daily_drift_log"]) <= abs(inp["daily_drift_raw_log"]) + 1e-12
    assert 0.0 <= inp["drift_shrink_factor"] <= 1.0
    assert inp["vol_estimator"].startswith("EWMA")


def test_forecast_naive_has_symmetric_pup():
    fc = forecast_price(_synthetic_closes(300), horizon_days=30, method="naive", with_skill=False)
    assert abs(fc["p_up"] - 0.5) < 1e-9
    assert fc["median"] == pytest.approx(fc["last_close"])


def test_compare_methods_scores_all():
    from atlas.forecast import compare_methods
    cmp = compare_methods(_synthetic_closes(400), horizon_days=30)
    assert {r["method"] for r in cmp["methods"]} == {"naive", "drift", "blend"}
    assert cmp["best_method"] in ("naive", "drift", "blend")
    assert cmp["skill_measured"] is True


def test_compare_methods_measures_skill_on_compact_history():
    # ~100 bars (Alpha Vantage 'compact') must still yield a MEASURED, if noisy,
    # skill — not null and not a fabricated "no edge" verdict.
    from atlas.forecast import compare_methods, forecast_price
    closes = _synthetic_closes(100)
    fc = forecast_price(closes, horizon_days=30)
    assert fc["skill"] is not None and fc["skill"]["noise_dominated"] is True
    cmp = compare_methods(closes, horizon_days=30)
    assert cmp["skill_measured"] is True
    assert cmp["noise_dominated"] is True
    assert "noise-dominated" in cmp["note"]


def test_compare_methods_unmeasured_never_claims_no_edge():
    # Just above the 60-bar forecast floor but too short for a 30-day walk-forward
    # (min 40-bar fitting window + 21 steps + folds) => skill cannot be measured.
    # The verdict must NOT assert "no edge".
    from atlas.forecast import compare_methods
    cmp = compare_methods(_synthetic_closes(62), horizon_days=30)
    assert cmp["skill_measured"] is False
    assert cmp["best_method"] == "naive"
    assert "insufficient history" in cmp["note"]
    assert "beats a random walk" not in cmp["note"]
    assert "unvalidated" in cmp["note"]


def test_registry_compare_and_query(tmp_path):
    reg = ToolRegistry()
    cmp = reg.compare_forecast_methods("AAA", horizon_days=30)
    assert "best_method" in cmp
    store = PredictionStore(str(tmp_path / "s.json"))
    run_daily_report(reg, universe="nasdaq5", store=store)
    q = reg.query_predictions(store_path=str(tmp_path / "s.json"), resolved=False)
    assert q["count"] == 5 and all(not r["resolved"] for r in q["predictions"])


# --------------------------------------------------------------------------- #
# universe
# --------------------------------------------------------------------------- #
def test_universe_static_snapshot():
    u = get_universe("nasdaq10")
    assert len(u["constituents"]) == 10
    assert u["ranking_source"] == "static_snapshot"
    assert u["as_of"]


def test_universe_unknown_errors():
    u = get_universe("does_not_exist")
    assert "error" in u and u["constituents"] == []


def test_universe_refresh_without_feed_falls_back():
    u = get_universe("nasdaq10", refresh=True, provider=None)
    assert u["ranking_source"] == "static_snapshot"
    assert any("no fundamentals feed" in n for n in u["notes"])


def test_universe_live_rerank():
    class FundProvider:
        source = "fund"
        _caps = None

        def get_fundamentals(self, sym):
            caps = {"AAA": "300", "BBB": "100", "CCC": "200"}
            return {"MarketCapitalization": caps.get(sym, "50")}

    # Monkey-patch the snapshot to a 3-name universe by using nasdaq5 subset? Use
    # a provider that covers the real candidates.
    import atlas.universe as U
    U._SNAPSHOTS["_test3"] = {"as_of": "2026-01-01", "description": "t",
                              "constituents": ["AAA", "BBB", "CCC"]}
    try:
        u = get_universe("_test3", refresh=True, provider=FundProvider())
        assert u["ranking_source"] == "live_market_cap"
        assert u["constituents"] == ["AAA", "CCC", "BBB"]  # 300 > 200 > 100
    finally:
        del U._SNAPSHOTS["_test3"]


def test_list_universes():
    assert "nasdaq10" in list_universes()


# --------------------------------------------------------------------------- #
# prediction store
# --------------------------------------------------------------------------- #
def test_store_log_and_persist(tmp_path):
    p = str(tmp_path / "s.json")
    store = PredictionStore(p)
    store.log_prediction(run_id="r1", symbol="AAA", asof="2026-01-01", horizon_days=30,
                         last_close=100.0, median=105.0, interval_80=[95, 115],
                         interval_95=[90, 120], p_up=0.6, method="drift", skill_score=0.1)
    assert store.save()
    reloaded = PredictionStore(p)
    assert len(reloaded.records) == 1
    assert reloaded.records[0]["target_date"] == "2026-01-31"


def test_store_log_idempotent_per_run(tmp_path):
    store = PredictionStore(str(tmp_path / "s.json"))
    for _ in range(3):
        store.log_prediction(run_id="r1", symbol="AAA", asof="2026-01-01", horizon_days=30,
                             last_close=100.0, median=105.0, interval_80=[95, 115],
                             interval_95=[90, 120], p_up=0.6, method="drift")
    assert len(store.records) == 1  # same (run_id, symbol) replaces


def test_store_resolve_and_accuracy(tmp_path):
    store = PredictionStore(str(tmp_path / "s.json"))
    store.log_prediction(run_id="r1", symbol="AAA", asof="2026-01-01", horizon_days=30,
                         last_close=100.0, median=105.0, interval_80=[95, 115],
                         interval_95=[90, 120], p_up=0.6, method="drift")
    # Target is 2026-01-31; realised = last bar AT OR BEFORE it (2026-01-30 = 108),
    # an as-of join per §22, and only because the series extends past the target.
    series = [("2026-01-20", 100.0), ("2026-01-30", 108.0), ("2026-02-10", 110.0)]
    res = store.resolve(lambda s: series, asof="2026-02-15")
    assert res["resolved_now"] == 1 and res["open_remaining"] == 0
    rec = store.resolved()[0]
    assert rec["realized_close"] == 108.0 and rec["realized_date"] == "2026-01-30"
    acc = store.accuracy_stats()
    assert acc["resolved_count"] == 1
    assert acc["sufficient"] is False  # < 10
    assert acc["mape_model_pct"] == pytest.approx(abs(105 - 108) / 108 * 100)
    assert acc["model_beats_naive"] is True
    assert acc["directional_accuracy"] == 1.0


def test_store_resolve_uses_last_bar_before_target(tmp_path):
    store = PredictionStore(str(tmp_path / "s.json"))
    store.log_prediction(run_id="r1", symbol="AAA", asof="2026-01-01", horizon_days=30,
                         last_close=100.0, median=105.0, interval_80=[95, 115],
                         interval_95=[90, 120], p_up=0.6, method="drift")
    # Target 2026-01-31 falls on a gap; the bar AFTER it (Feb 2) must NOT be used.
    series = [("2026-01-28", 102.0), ("2026-02-02", 130.0)]
    store.resolve(lambda s: series, asof="2026-03-01")
    rec = store.resolved()[0]
    assert rec["realized_close"] == 102.0  # last bar <= target, not the Feb-2 spike


def test_store_does_not_resolve_before_target(tmp_path):
    store = PredictionStore(str(tmp_path / "s.json"))
    store.log_prediction(run_id="r1", symbol="AAA", asof="2026-01-01", horizon_days=30,
                         last_close=100.0, median=105.0, interval_80=[95, 115],
                         interval_95=[90, 120], p_up=0.6, method="drift")
    # Series ends before the 2026-01-31 target -> stays open.
    series = [("2026-01-10", 101.0), ("2026-01-20", 102.0)]
    res = store.resolve(lambda s: series)
    assert res["resolved_now"] == 0 and res["open_remaining"] == 1


def test_accuracy_stats_empty():
    acc = accuracy_stats([])
    assert acc["resolved_count"] == 0 and acc["sufficient"] is False


def test_target_date_helper():
    assert target_date("2026-01-01", 30).isoformat() == "2026-01-31"


# --------------------------------------------------------------------------- #
# daily orchestrator
# --------------------------------------------------------------------------- #
def test_run_daily_report_synthetic():
    reg = ToolRegistry()
    rep = run_daily_report(reg, universe="nasdaq5", store=None)
    assert rep["kind"] == "atlas_daily_report"
    assert len(rep["rows"]) == 5
    assert rep["simulated"] is True  # synthetic feed
    assert rep["ranking_source"] == "static_snapshot"
    assert rep["summary"]["count"] == 5
    # Every successful row carries an interval and a P(up) — a distribution.
    for r in rep["rows"]:
        if "error" not in r:
            assert r["interval_80"][0] < r["median"] < r["interval_80"][1]
            assert 0.0 <= r["p_up"] <= 1.0


def test_run_daily_report_persists_and_resolves(tmp_path):
    # The report anchors to the newest bar the feed has. To resolve later, the
    # feed must have advanced *past* each prediction's target — so the report
    # sees a truncated history, and resolution sees more future bars.
    closes = _synthetic_closes(300)
    report_reg = ToolRegistry(DatedProvider(closes[:200], start_date="2026-01-01"))
    store = PredictionStore(str(tmp_path / "s.json"))
    rep = run_daily_report(report_reg, universe="nasdaq5", store=store, asof="2026-07-19")
    assert rep["persisted"] is True
    assert rep["simulated"] is False
    assert len(store.records) >= 1

    # Feed has advanced ~100 days: predictions' 30-day horizons have elapsed.
    resolve_reg = ToolRegistry(DatedProvider(closes[:300], start_date="2026-01-01"))
    res = resolve_predictions(resolve_reg, store)
    assert res["resolved_now"] >= 1
    acc = forecast_accuracy(store, horizon_days=30)
    assert acc["resolved_count"] >= 1
    assert "directional_accuracy" in acc


def test_daily_report_bad_universe():
    reg = ToolRegistry()
    rep = run_daily_report(reg, universe="nope", store=None)
    assert "error" in rep


def test_render_report_text_has_banners_and_disclaimer():
    reg = ToolRegistry()
    rep = run_daily_report(reg, universe="nasdaq5", store=None)
    text = render_report(rep, "text")
    assert "SIMULATED DATA" in text  # synthetic feed banner
    assert "not financial advice" in text
    assert "REALISED ACCURACY" in text
    # No forbidden target language in the forecast rows/summary. (The disclaimer
    # legitimately says forecasts are "not price targets" — check the body only.)
    body = text.split("Educational analysis")[0].lower()
    for banned in ("will reach", "price target", "target of", "on track for"):
        assert banned not in body


def test_render_report_markdown_and_html():
    reg = ToolRegistry()
    rep = run_daily_report(reg, universe="nasdaq5", store=None)
    md = render_report(rep, "markdown")
    assert md.startswith(">") or md.startswith("#")
    assert "| Symbol |" in md
    html = render_report(rep, "html")
    assert html.startswith("<!doctype")
    assert "<title>ATLAS Daily Report</title>" in html
    assert "banner" in html  # simulated banner div present


def test_report_from_store_replay(tmp_path):
    reg = ToolRegistry()
    store = PredictionStore(str(tmp_path / "s.json"))
    run_daily_report(reg, universe="nasdaq5", store=store)
    replay = report_from_store(store)
    assert replay["kind"] == "atlas_daily_report_replay"
    assert len(replay["rows"]) == 5


def test_report_from_store_empty(tmp_path):
    store = PredictionStore(str(tmp_path / "s.json"))
    assert "error" in report_from_store(store)


def test_report_from_store_is_renderable(tmp_path):
    reg = ToolRegistry()
    store = PredictionStore(str(tmp_path / "s.json"))
    run_daily_report(reg, universe="nasdaq5", store=store, asof="2026-08-18")
    replay = report_from_store(store)
    # run_id (with a dashed date) parses back to the universe + run date.
    assert replay["universe"] == "nasdaq5"
    assert replay["run_date"] == "2026-08-18"
    text = render_report(replay, "text")
    assert "ATLAS DAILY REPORT — NASDAQ5" in text
    assert render_report(replay, "html").startswith("<!doctype")


# --------------------------------------------------------------------------- #
# registry tool surface
# --------------------------------------------------------------------------- #
def test_cli_daily_writes_unicode_to_cp1252_stream(tmp_path):
    """Regression: the ⚠ banner / en-dashes must not crash on a cp1252 stdout
    (Windows console default). ``main`` forces UTF-8 on stdout."""
    import io
    import sys

    from atlas.cli import main

    out_path = tmp_path / "report.html"
    saved = sys.stdout
    try:
        sys.stdout = io.TextIOWrapper(open(out_path, "wb"), encoding="cp1252")
        rc = main(["daily", "run", "--report-format", "html", "--no-store"])
        sys.stdout.flush()
    finally:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.stdout = saved
    assert rc == 0
    data = out_path.read_text(encoding="utf-8")
    assert "⚠" in data  # the warning glyph survived, UTF-8 encoded
    assert data.startswith("<!doctype")


def test_registry_daily_tools():
    reg = ToolRegistry()
    u = reg.get_universe("nasdaq10")
    assert len(u["constituents"]) == 10
    f = reg.forecast_price("AAA", horizon_days=30)
    assert "median" in f and "skill" in f
    rep = reg.run_daily_report(universe="nasdaq5")
    assert rep["summary"]["count"] == 5
    assert reg.render_report(rep, "text").count("\n") > 5
