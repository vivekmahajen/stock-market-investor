"""Tests for StooqProvider — fully offline via an injected fetcher.

The network seam is replaced with a fake that returns canned Stooq CSV payloads,
so parsing, symbol mapping, error handling, and the downstream pipeline are
exercised without any live request.
"""
import pytest

from atlas.analysis import analyze
from atlas.data import StooqProvider
from atlas.tools import ToolRegistry

_SAMPLE_CSV = """Date,Open,High,Low,Close,Volume
2024-01-02,100.0,102.0,99.5,101.0,1000000
2024-01-03,101.0,103.5,100.5,103.0,1200000
2024-01-04,103.0,104.0,101.0,101.5,900000
2024-01-05,101.5,102.5,100.0,102.0,1100000
"""


def _provider(csv_text=_SAMPLE_CSV):
    calls = {"urls": []}

    def fake_fetch(url):
        calls["urls"].append(url)
        return csv_text

    return StooqProvider(fetch=fake_fetch), calls


def test_symbol_mapping():
    p, _ = _provider()
    assert p.to_stooq_symbol("AAPL") == "aapl.us"
    assert p.to_stooq_symbol("^spx") == "^spx"          # index passthrough
    assert p.to_stooq_symbol("aapl.us") == "aapl.us"    # already suffixed
    assert p.to_stooq_symbol("EURUSD") == "eurusd.us"


def test_url_construction_and_interval():
    p, calls = _provider()
    p.get_ohlcv("AAPL", "1d", 10)
    assert "s=aapl.us" in calls["urls"][0]
    assert "i=d" in calls["urls"][0]


def test_intraday_rejected():
    p, _ = _provider()
    with pytest.raises(ValueError):
        p.get_ohlcv("AAPL", "5m", 10)


def test_parse_produces_ohlcv():
    p, _ = _provider()
    series = p.get_ohlcv("AAPL", "1d", 250)
    assert len(series) == 4
    assert series.symbol == "AAPL"
    assert series.close[-1] == 102.0
    assert p.simulated is False  # the provider flags this as real data


def test_tail_lookback():
    p, _ = _provider()
    series = p.get_ohlcv("AAPL", "1d", 2)
    assert len(series) == 2
    assert series.close[0] == 101.5


def test_skips_sentinel_rows():
    csv = _SAMPLE_CSV + "2024-01-08,N/D,N/D,N/D,N/D,N/D\n"
    p, _ = _provider(csv)
    series = p.get_ohlcv("AAPL", "1d", 250)
    assert len(series) == 4          # sentinel row skipped
    assert p.last_skipped_rows == 1


def test_rate_limit_message_raises():
    p, _ = _provider("Exceeded the daily hits limit")
    with pytest.raises(RuntimeError, match="rate limit"):
        p.get_ohlcv("AAPL", "1d", 10)


def test_unexpected_response_raises():
    p, _ = _provider("<html>Not found</html>")
    with pytest.raises(RuntimeError):
        p.get_ohlcv("AAPL", "1d", 10)


def test_quote_from_last_bar():
    p, _ = _provider()
    q = p.get_quote("AAPL")
    assert q.last == 102.0
    assert q.bid < q.last < q.ask
    assert q.symbol == "AAPL"


def test_provenance_flags_real_source():
    p, _ = _provider()
    prov = p.provenance("get_ohlcv", "AAPL", "1d", "250")
    assert prov.source == "stooq"
    assert prov.simulated is False


def test_registry_and_analyze_with_stooq():
    p, _ = _provider(_SAMPLE_CSV * 40)  # enough bars for indicators
    # Rebuild a longer, valid series with unique dates for the analysis pipeline.
    rows = ["Date,Open,High,Low,Close,Volume"]
    price = 100.0
    from datetime import date, timedelta
    d = date(2023, 1, 2)
    for i in range(120):
        price *= 1.002
        rows.append(f"{d + timedelta(days=i)},{price:.2f},{price*1.01:.2f},{price*0.99:.2f},{price:.2f},1000000")
    csv = "\n".join(rows) + "\n"
    prov = StooqProvider(fetch=lambda url: csv)
    reg = ToolRegistry(prov)
    out = analyze("AAPL", registry=reg, lookback=120)
    assert out["symbol"] == "AAPL"
    assert out["data_is_simulated"] is False
    assert out["atlas_score"] is not None
