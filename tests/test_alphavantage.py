"""Tests for AlphaVantageProvider — fully offline via an injected fetcher."""
import pytest

from atlas.analysis import analyze
from atlas.data import AlphaVantageProvider
from atlas.tools import ToolRegistry

# Alpha Vantage daily CSV: newest-first, header 'timestamp,open,high,low,close,volume'.
_DAILY_CSV = """timestamp,open,high,low,close,volume
2024-01-05,181.99,182.76,180.17,181.18,62303000
2024-01-04,182.15,183.09,180.88,181.91,71983600
2024-01-03,184.22,185.88,183.43,184.25,58414500
2024-01-02,187.15,188.44,183.89,185.64,82488700
"""

_QUOTE_CSV = """symbol,open,high,low,price,volume,latestDay,previousClose,change,changePercent
AAPL,181.99,182.76,180.17,181.18,62303000,2024-01-05,181.91,-0.73,-0.40%
"""

_RATE_LIMIT_JSON = '{ "Information": "We have detected your API key ... 25 requests per day." }'
_ERROR_JSON = '{ "Error Message": "Invalid API call. Please retry or visit the documentation." }'


def _provider(text):
    calls = {"urls": []}

    def fake_fetch(url):
        calls["urls"].append(url)
        return text

    return AlphaVantageProvider(api_key="TESTKEY", fetch=fake_fetch), calls


def test_daily_parse_sorted_ascending():
    p, _ = _provider(_DAILY_CSV)
    s = p.get_ohlcv("AAPL", "1d", 250)
    assert len(s) == 4
    # newest-first input becomes chronological output
    assert s.close[0] == 185.64
    assert s.close[-1] == 181.18


def test_url_has_function_and_key():
    p, calls = _provider(_DAILY_CSV)
    p.get_ohlcv("AAPL", "1d", 50)
    url = calls["urls"][0]
    assert "function=TIME_SERIES_DAILY" in url
    assert "apikey=TESTKEY" in url
    assert "datatype=csv" in url
    assert "outputsize=compact" in url  # lookback <= 100


def test_free_tier_never_requests_full():
    # outputsize=full is a PREMIUM feature on Alpha Vantage (daily and intraday) —
    # a free key requesting full gets an error, not compact — so the free tier
    # must stay compact even for large lookbacks.
    p, calls = _provider(_DAILY_CSV)  # premium defaults to False
    p.get_ohlcv("AAPL", "1d", 500)
    assert "outputsize=compact" in calls["urls"][0]
    assert "outputsize=full" not in calls["urls"][0]


def test_free_tier_intraday_stays_compact():
    calls = {"urls": []}
    intraday = "timestamp,open,high,low,close,volume\n2024-01-05 19:55:00,1,2,0.5,1.5,100\n"
    p = AlphaVantageProvider(api_key="K", premium=False,
                             fetch=lambda u: (calls["urls"].append(u) or intraday))
    p.get_ohlcv("AAPL", "1h", 500)
    assert "outputsize=compact" in calls["urls"][0]
    assert "outputsize=full" not in calls["urls"][0]


def test_premium_uses_full_for_large_lookback():
    calls = {"urls": []}
    p = AlphaVantageProvider(api_key="K", premium=True, fetch=lambda u: (calls["urls"].append(u) or _DAILY_CSV))
    p.get_ohlcv("AAPL", "1d", 500)
    assert "outputsize=full" in calls["urls"][0]


def test_intraday_interval_mapping():
    p, calls = _provider("timestamp,open,high,low,close,volume\n2024-01-05 19:55:00,1,2,0.5,1.5,100\n")
    p.get_ohlcv("AAPL", "5m", 50)
    url = calls["urls"][0]
    assert "function=TIME_SERIES_INTRADAY" in url
    assert "interval=5min" in url


def test_intraday_timestamp_parsed():
    p, _ = _provider("timestamp,open,high,low,close,volume\n2024-01-05 19:55:00,1,2,0.5,1.5,100\n")
    s = p.get_ohlcv("AAPL", "5m", 50)
    assert s.ts[0].hour == 19 and s.ts[0].minute == 55


def test_weekly_and_monthly_functions():
    pw, cw = _provider(_DAILY_CSV)
    pw.get_ohlcv("AAPL", "1w", 50)
    assert "function=TIME_SERIES_WEEKLY" in cw["urls"][0]
    pm, cm = _provider(_DAILY_CSV)
    pm.get_ohlcv("AAPL", "1mo", 50)
    assert "function=TIME_SERIES_MONTHLY" in cm["urls"][0]


def test_unsupported_timeframe():
    p, _ = _provider(_DAILY_CSV)
    with pytest.raises(ValueError):
        p.get_ohlcv("AAPL", "3s", 50)


def test_rate_limit_json_raises():
    p, _ = _provider(_RATE_LIMIT_JSON)
    with pytest.raises(RuntimeError, match="rate limit|notice"):
        p.get_ohlcv("AAPL", "1d", 50)


def test_error_json_raises():
    p, _ = _provider(_ERROR_JSON)
    with pytest.raises(RuntimeError, match="error"):
        p.get_ohlcv("AAPL", "1d", 50)


def test_quote_parse():
    p, _ = _provider(_QUOTE_CSV)
    q = p.get_quote("AAPL")
    assert q.last == 181.18
    assert q.bid < q.last < q.ask


def test_missing_api_key_raises_only_for_real_fetch():
    # No key and no injected fetch -> must raise a clear error before any network.
    p = AlphaVantageProvider(api_key=None)
    with pytest.raises(ValueError, match="API key required"):
        p.get_ohlcv("AAPL", "1d", 50)


def test_provenance_real_source():
    p, _ = _provider(_DAILY_CSV)
    prov = p.provenance("get_ohlcv", "AAPL", "1d", "250")
    assert prov.source == "alphavantage"
    assert prov.simulated is False


def test_analyze_pipeline_with_alphavantage():
    # Build a long synthetic-but-AV-formatted CSV (newest-first) for the pipeline.
    from datetime import date, timedelta

    rows = ["timestamp,open,high,low,close,volume"]
    price = 100.0
    d = date(2023, 1, 2)
    series = []
    for i in range(130):
        price *= 1.001
        series.append((d + timedelta(days=i), price))
    for dt, px in reversed(series):  # newest first
        rows.append(f"{dt},{px:.2f},{px*1.01:.2f},{px*0.99:.2f},{px:.2f},1000000")
    csv = "\n".join(rows) + "\n"
    reg = ToolRegistry(AlphaVantageProvider(api_key="K", fetch=lambda url: csv))
    out = analyze("AAPL", registry=reg, lookback=130)
    assert out["symbol"] == "AAPL"
    assert out["data_is_simulated"] is False
    assert out["atlas_score"] is not None
