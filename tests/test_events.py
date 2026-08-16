"""Tests for the event-risk calendar (earnings parsing, risk labels, wiring)."""
from datetime import date

import pytest

from atlas.analysis import build_signal
from atlas.data import AlphaVantageProvider
from atlas.events import (
    build_event_risk,
    event_risk_note,
    nearest_event,
    parse_earnings_csv,
)
from atlas.tools import ToolRegistry

_EARNINGS_CSV = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
MSFT,Microsoft Corp,2026-08-20,2026-06-30,3.55,USD
MSFT,Microsoft Corp,2026-11-19,2026-09-30,,USD
"""


def test_parse_earnings_csv():
    rows = parse_earnings_csv(_EARNINGS_CSV)
    assert len(rows) == 2
    assert rows[0]["reportDate"] == "2026-08-20"
    assert rows[0]["estimate"] == "3.55"
    assert rows[1]["estimate"] is None


def test_parse_empty_feed():
    assert parse_earnings_csv("symbol,name,reportDate,fiscalDateEnding,estimate,currency\n") == []
    assert parse_earnings_csv("") == []
    assert parse_earnings_csv("{}") == []  # JSON error page -> no rows


def test_build_event_risk_high_medium_low():
    rows = parse_earnings_csv(_EARNINGS_CSV)
    # asof 6 days before the first report -> high risk.
    ev = build_event_risk(rows, date(2026, 8, 14))
    assert ev[0]["type"] == "earnings"
    assert ev[0]["days_away"] == 6
    assert ev[0]["risk"] == "high"


def test_build_event_risk_medium():
    rows = parse_earnings_csv(_EARNINGS_CSV)
    ev = build_event_risk(rows, date(2026, 8, 1))  # 19 days away
    assert ev[0]["days_away"] == 19
    assert ev[0]["risk"] == "medium"


def test_build_event_risk_excludes_past_and_far():
    rows = parse_earnings_csv(_EARNINGS_CSV)
    # asof after the first report and >45d before the second -> only far one excluded.
    ev = build_event_risk(rows, date(2026, 8, 21), window_days=45)
    # first report (8/20) is in the past -> excluded; second (11/19) is >45d -> excluded.
    assert ev == []


def test_event_risk_note_high():
    rows = parse_earnings_csv(_EARNINGS_CSV)
    ev = build_event_risk(rows, date(2026, 8, 18))  # 2 days
    note = event_risk_note(ev)
    assert "HIGH event risk" in note


def test_nearest_event_empty():
    assert nearest_event([]) is None


def test_provider_get_earnings_calendar():
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: _EARNINGS_CSV)
    rows = p.get_earnings_calendar("MSFT")
    assert len(rows) == 2


def test_provider_earnings_rate_limit():
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: '{"Information": "25 per day"}')
    with pytest.raises(RuntimeError):
        p.get_earnings_calendar("MSFT")


def test_registry_earnings_and_unsupported_provider():
    reg = ToolRegistry(AlphaVantageProvider(api_key="K", fetch=lambda url: _EARNINGS_CSV))
    out = reg.get_earnings_calendar("MSFT")
    assert "earnings" in out and len(out["earnings"]) == 2

    from atlas.data import SyntheticProvider
    reg2 = ToolRegistry(SyntheticProvider())
    assert "error" in reg2.get_earnings_calendar("MSFT")


def test_build_signal_defers_on_high_event_risk():
    events = [{"type": "earnings", "date": "2026-08-18", "days_away": 3, "risk": "high", "estimate": None}]
    sig = build_signal("MSFT", entry=100, stop=95, targets=[110, 120], direction="long",
                       account_equity=100_000, events=events)
    assert any("HIGH event risk" in w for w in sig["warnings"])
