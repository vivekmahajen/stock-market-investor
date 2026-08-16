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
    assert "<title>ATLAS Dashboard</title>" in DASHBOARD_HTML
    assert "/api/analyze" in DASHBOARD_HTML
    assert "http://" not in DASHBOARD_HTML.split("</style>")[0]  # no external CSS/hosts in styles


def test_run_analysis_with_events_flag_via_synthetic_notes():
    # Synthetic provider has no earnings feed -> graceful note, not a crash.
    status, out = run_analysis({"symbol": "AAA", "source": "synthetic", "events": "1"})
    assert status == 200
    assert any("events unchecked" in n for n in out["notes"])
