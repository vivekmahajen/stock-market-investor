"""Tests for the web dashboard's request handler and page (no sockets)."""
import json

import pytest

from atlas.web import DASHBOARD_HTML, build_registry, run_analysis
from atlas.data import AlphaVantageProvider, CSVProvider, SyntheticProvider


def test_run_analysis_synthetic_ok():
    status, out = run_analysis({"symbol": "AAA", "source": "synthetic"})
    assert status == 200
    assert out["symbol"] == "AAA"
    assert out["atlas_score"] is not None
    assert out["data_is_simulated"] is True


def test_run_analysis_requires_symbol():
    status, out = run_analysis({"source": "synthetic"})
    assert status == 400
    assert "error" in out


def test_run_analysis_symbol_uppercased():
    _, out = run_analysis({"symbol": "aaa", "source": "synthetic"})
    assert out["symbol"] == "AAA"


def test_run_analysis_is_json_serialisable():
    _, out = run_analysis({"symbol": "AAA", "source": "synthetic"})
    json.dumps(out, default=str)  # must not raise


def test_run_analysis_toggles_default_off():
    _, out = run_analysis({"symbol": "AAA", "source": "synthetic"})
    # No feeds requested -> those sub-scores stay null with an explanatory note.
    assert out["subscores"]["fundamental"] is None
    assert any("with_fundamentals" in n for n in out["notes"])


def test_build_registry_types():
    assert isinstance(build_registry("synthetic").provider, SyntheticProvider)
    assert isinstance(build_registry("alphavantage", api_key="K").provider, AlphaVantageProvider)
    assert isinstance(build_registry("csv", csv_dir="./data").provider, CSVProvider)


def test_run_analysis_bad_source_falls_back_to_synthetic():
    # Unknown source -> synthetic (never crashes).
    status, out = run_analysis({"symbol": "AAA", "source": "nonsense"})
    assert status == 200
    assert out["data_is_simulated"] is True


def test_dashboard_html_is_selfcontained():
    # No external network dependencies (CDNs) in the page.
    assert "<title>ATLAS Charts</title>" in DASHBOARD_HTML
    assert "/api/analyze" in DASHBOARD_HTML and "/api/chart" in DASHBOARD_HTML
    assert "<canvas" in DASHBOARD_HTML  # interactive chart present
    assert "http://" not in DASHBOARD_HTML.split("</style>")[0]  # no external CSS/hosts in styles


def test_build_chart_data_shape():
    from atlas.web import build_chart_data
    status, d = build_chart_data({"symbol": "AAA", "source": "synthetic", "lookback": "200"})
    assert status == 200
    assert len(d["bars"]) == 200
    assert set(d["bars"][0]) == {"t", "o", "h", "l", "c", "v"}
    assert "ema20" in d["overlays"] and "bb_upper" in d["overlays"]
    assert "support" in d["levels"] and "resistance" in d["levels"]
    assert "trendlines" in d and "fibonacci" in d and "patterns" in d


def test_build_chart_data_requires_symbol():
    from atlas.web import build_chart_data
    status, d = build_chart_data({"source": "synthetic"})
    assert status == 400 and "error" in d


def test_build_scan_data_ranks_results():
    from atlas.web import build_scan_data
    status, d = build_scan_data({"symbols": "AAA,BBB,CCC,DDD,EEE", "source": "synthetic"})
    assert status == 200
    scores = [r["composite_score"] for r in d["results"]]
    assert scores == sorted(scores, reverse=True)
    assert "metrics" in d["results"][0] and "liquidity_flags" in d["results"][0]


def test_build_scan_data_filter_narrows():
    from atlas.web import build_scan_data
    _, allr = build_scan_data({"symbols": "AAA,BBB,CCC,DDD,EEE", "source": "synthetic"})
    _, filt = build_scan_data({"symbols": "AAA,BBB,CCC,DDD,EEE", "source": "synthetic", "above_ema50": "1"})
    assert filt["matched"] <= allr["matched"]
    for r in filt["results"]:
        assert r["metrics"]["above_ema50"] is True


def test_build_scan_data_requires_symbols():
    from atlas.web import build_scan_data
    status, d = build_scan_data({"source": "synthetic"})
    assert status == 400 and "error" in d


def test_dashboard_has_scanner_tab():
    assert "tabScan" in DASHBOARD_HTML and "/api/scan" in DASHBOARD_HTML


def test_build_backtest_data_shape():
    from atlas.web import build_backtest_data
    status, d = build_backtest_data({"symbol": "AAA", "source": "synthetic",
                                     "fast": "20", "slow": "50", "lookback": "750"})
    assert status == 200
    assert "metrics" in d and "verdict" in d
    assert len(d["equity_curve"]) > 0 and len(d["bars"]) > 0
    for t in d["trades"]:
        assert "entry_i" in t and "direction" in t and "pnl" in t


def test_build_backtest_data_robustness():
    from atlas.web import build_backtest_data
    _, d = build_backtest_data({"symbol": "AAA", "source": "synthetic",
                               "robustness": "sensitivity", "lookback": "750"})
    assert "robustness" in d and "assessment" in d["robustness"]


def test_build_backtest_data_requires_symbol():
    from atlas.web import build_backtest_data
    status, d = build_backtest_data({"source": "synthetic"})
    assert status == 400 and "error" in d


def test_dashboard_has_backtester_tab():
    assert "tabBt" in DASHBOARD_HTML and "/api/backtest" in DASHBOARD_HTML
    assert "btEquity" in DASHBOARD_HTML  # equity-curve canvas present


def test_run_analysis_with_events_flag_via_synthetic_notes():
    # Synthetic provider has no earnings feed -> graceful note, not a crash.
    status, out = run_analysis({"symbol": "AAA", "source": "synthetic", "events": "1"})
    assert status == 200
    assert any("events unchecked" in n for n in out["notes"])
