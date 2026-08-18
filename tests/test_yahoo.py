"""Tests for the Yahoo Finance provider (pure parsing, offline)."""
from __future__ import annotations

import json

import pytest

from atlas.data import YahooProvider


def _chart(timestamps, o, h, l, c, v, error=None):
    return json.dumps({"chart": {
        "error": error,
        "result": None if error else [{
            "meta": {"symbol": "AAPL"},
            "timestamp": timestamps,
            "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]},
        }],
    }})


def _provider(payload):
    calls = {"urls": []}
    p = YahooProvider(fetch=lambda u: (calls["urls"].append(u) or payload))
    return p, calls


def test_daily_url_uses_period_window_not_range_max():
    # range=max silently downsamples long histories; an explicit period window
    # is served at true daily resolution.
    p = YahooProvider(fetch=lambda u: "{}")
    url = p._url("AAPL", "1d", 0)
    assert "range=max" not in url
    assert "interval=1d" in url and "period1=" in url and "period2=" in url
    assert "includeadjustedclose=true" in url.lower()


def test_intraday_uses_bounded_range():
    p = YahooProvider(fetch=lambda u: "{}")
    assert "range=60d" in p._url("AAPL", "5m") and "interval=5m" in p._url("AAPL", "5m")
    assert "range=730d" in p._url("AAPL", "1h")


def test_downsampled_daily_data_is_refused():
    # Monthly-spaced timestamps labelled 1d must be caught, not silently used.
    monthly = [1577836800 + i * 31 * 86400 for i in range(6)]
    c = [10, 11, 12, 13, 14, 15]
    p, _ = _provider(_chart(monthly, c, [x + 1 for x in c], [x - 1 for x in c], c, [1] * 6))
    with pytest.raises(RuntimeError, match="downsampled"):
        p.get_ohlcv("AAPL", "1d", 0)


def test_parse_basic_series():
    p, _ = _provider(_chart([1577836800, 1577923200],
                            [74.0, 74.5], [75.0, 75.5], [73.5, 74.0], [74.8, 75.2], [100, 110]))
    s = p.get_ohlcv("AAPL", "1d", 0)
    assert len(s) == 2
    assert s.close[0] == 74.8 and s.ts[0].date().isoformat() == "2020-01-01"


def test_null_rows_are_skipped_not_guessed():
    # Day-apart timestamps so daily bars land on distinct dates.
    p, _ = _provider(_chart([1577836800, 1577923200, 1578009600],
                            [10, None, 12], [11, 11, 13], [9, 9, 11], [10.5, None, 12.5], [1, 2, 3]))
    s = p.get_ohlcv("AAPL", "1d", 0)
    assert len(s) == 2 and p.last_skipped_rows == 1  # the null middle row dropped


def test_uses_adjusted_close_to_remove_split_jump():
    # Raw close halves on a 2:1 split (day 3); adjclose is continuous. The parsed
    # series must follow adjclose, so no fake ~-50% return survives.
    import math
    payload = json.dumps({"chart": {"error": None, "result": [{
        "meta": {},
        "timestamp": [1577836800, 1577923200, 1578009600, 1578096000],
        "indicators": {
            "quote": [{"open": [200, 202, 100, 101], "high": [201, 203, 101, 102],
                       "low": [199, 201, 99, 100], "close": [200, 202, 100, 101],
                       "volume": [1, 2, 3, 4]}],
            "adjclose": [{"adjclose": [100, 101, 100, 101]}],
        },
    }]}})
    p, _ = _provider(payload)
    s = p.get_ohlcv("AAPL", "1d", 0)
    closes = list(s.close)
    assert closes == [100.0, 101.0, 100.0, 101.0]  # adjusted, continuous
    max_move = max(abs(math.log(closes[i] / closes[i - 1])) for i in range(1, len(closes)))
    assert max_move < 0.05  # the raw -50% split jump is gone; OHLC back-adjusted too
    assert s.open[0] == 100.0  # open back-adjusted by adjclose/close = 100/200


def test_null_adjclose_bar_is_skipped_not_scale_mixed():
    # adjclose present for the series but null on one bar => that bar is skipped,
    # never filled from the raw (unadjusted) close, which would mix price scales.
    payload = json.dumps({"chart": {"error": None, "result": [{
        "meta": {},
        "timestamp": [1577836800, 1577923200, 1578009600],
        "indicators": {
            "quote": [{"open": [200, 202, 204], "high": [201, 203, 205],
                       "low": [199, 201, 203], "close": [200, 202, 204], "volume": [1, 2, 3]}],
            "adjclose": [{"adjclose": [100, None, 102]}],  # middle bar unadjusted
        },
    }]}})
    p, _ = _provider(payload)
    s = p.get_ohlcv("AAPL", "1d", 0)
    assert list(s.close) == [100.0, 102.0]  # the raw-202 bar dropped, not mixed in
    assert p.last_skipped_rows == 1


def test_error_envelope_raises():
    p, _ = _provider(_chart(None, None, None, None, None, None,
                            error={"code": "Not Found", "description": "No data found"}))
    with pytest.raises(RuntimeError, match="No data found"):
        p.get_ohlcv("ZZZZ", "1d", 0)


def test_non_json_raises():
    p, _ = _provider("<html>rate limited</html>")
    with pytest.raises(RuntimeError, match="non-JSON"):
        p.get_ohlcv("AAPL", "1d", 0)


def test_empty_result_raises():
    p, _ = _provider(json.dumps({"chart": {"error": None, "result": []}}))
    with pytest.raises(RuntimeError, match="No data returned"):
        p.get_ohlcv("AAPL", "1d", 0)


def test_provenance_is_real():
    p, _ = _provider(_chart([1577836800], [1], [2], [0.5], [1.5], [100]))
    prov = p.provenance("get_ohlcv", "AAPL", "1d", "0")
    assert prov.source == "yahoo" and prov.simulated is False


def test_unsupported_timeframe():
    p, _ = _provider(_chart([1], [1], [2], [0.5], [1.5], [100]))
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        p.get_ohlcv("AAPL", "3s", 0)


def test_fetch_cli_defaults_to_yahoo(tmp_path, monkeypatch, capsys):
    from atlas import cli
    from atlas.data import SyntheticProvider

    class FakeYahoo(SyntheticProvider):
        source = "yahoo"

    monkeypatch.setattr(cli, "YahooProvider", FakeYahoo)
    rc = cli.main(["fetch", "AAPL", "--out", str(tmp_path / "c"), "--lookback", "300"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "yahoo" and payload["written"][0]["bars"] == 300
