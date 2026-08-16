"""Tests for CSVProvider's auto-detecting parser across common file layouts."""
import pytest

from atlas.data import CSVProvider, parse_ohlcv_csv

# Native layout.
_NATIVE = """ts,open,high,low,close,volume
2024-01-02,100,102,99,101,1000
2024-01-03,101,103,100,102,1100
"""

# Stooq browser-download layout (newest-or-oldest, Date/Open/... headers).
_STOOQ = """Date,Open,High,Low,Close,Volume
2024-01-02,100,102,99,101,1000
2024-01-03,101,103,100,102,1100
"""

# Alpha Vantage layout (timestamp, newest-first).
_AV = """timestamp,open,high,low,close,volume
2024-01-03,101,103,100,102,1100
2024-01-02,100,102,99,101,1000
"""

# Yahoo layout with Adj Close and no plain close-only rows, US date format.
_YAHOO = """Date,Open,High,Low,Close,Adj Close,Volume
01/02/2024,100,102,99,101,100.5,1000
01/03/2024,101,103,100,102,101.5,1100
"""


def test_native_layout():
    s, skipped = parse_ohlcv_csv(_NATIVE, "TEST", "1d")
    assert len(s) == 2 and skipped == 0
    assert s.close[-1] == 102


def test_stooq_layout():
    s, _ = parse_ohlcv_csv(_STOOQ, "TEST", "1d")
    assert len(s) == 2
    assert s.open[0] == 100


def test_alphavantage_layout_sorted():
    s, _ = parse_ohlcv_csv(_AV, "TEST", "1d")
    # Newest-first input becomes chronological.
    assert s.close[0] == 101
    assert s.close[-1] == 102


def test_yahoo_us_dates_and_volume():
    s, _ = parse_ohlcv_csv(_YAHOO, "TEST", "1d")
    assert len(s) == 2
    assert s.volume[0] == 1000
    # 01/02/2024 (US m/d/y) parses to January 2.
    assert s.ts[0].month == 1 and s.ts[0].day == 2


def test_adj_close_used_when_no_close():
    text = "Date,Open,High,Low,Adj Close,Volume\n2024-01-02,100,102,99,101,1000\n"
    s, _ = parse_ohlcv_csv(text, "TEST", "1d")
    assert s.close[0] == 101


def test_missing_ohlc_raises():
    with pytest.raises(ValueError, match="Missing OHLC"):
        parse_ohlcv_csv("Date,Foo,Bar\n2024-01-02,1,2\n", "TEST", "1d")


def test_no_date_column_raises():
    with pytest.raises(ValueError, match="date/timestamp"):
        parse_ohlcv_csv("open,high,low,close\n1,2,0.5,1.5\n", "TEST", "1d")


def test_sentinel_rows_skipped():
    text = _NATIVE + "2024-01-04,N/D,N/D,N/D,N/D,N/D\n"
    s, skipped = parse_ohlcv_csv(text, "TEST", "1d")
    assert len(s) == 2 and skipped == 1


def test_volume_optional():
    text = "Date,Open,High,Low,Close\n2024-01-02,100,102,99,101\n"
    s, _ = parse_ohlcv_csv(text, "TEST", "1d")
    assert s.volume[0] == 0.0


def test_provider_reads_file_and_filename_fallback(tmp_path):
    # File named just <symbol>.csv (no timeframe) should still resolve.
    (tmp_path / "AAPL.csv").write_text(_STOOQ)
    prov = CSVProvider(str(tmp_path))
    s = prov.get_ohlcv("AAPL", "1d", 250)
    assert len(s) == 2
    assert prov.simulated is False


def test_provider_missing_file_raises(tmp_path):
    prov = CSVProvider(str(tmp_path))
    with pytest.raises(FileNotFoundError, match="Looked for"):
        prov.get_ohlcv("NOPE", "1d", 10)
