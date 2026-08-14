"""Tests for the indicator library — checked against hand-computed values."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas import indicators as ind
from atlas.types import OHLCV, Bar


def _series(closes, highs=None, lows=None, opens=None, vols=None):
    n = len(closes)
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    opens = opens or list(closes)
    vols = vols or [1000.0] * n
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), opens[i], max(highs[i], opens[i], closes[i]),
            min(lows[i], opens[i], closes[i]), closes[i], vols[i])
        for i in range(n)
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


def test_sma_basic():
    out = ind.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_sma_too_short():
    assert ind.sma([1, 2], 5) == [None, None]


def test_ema_seed_is_sma():
    out = ind.ema([1, 2, 3, 4, 5], 3)
    # First defined value (index 2) equals the SMA of the first 3.
    assert out[2] == pytest.approx(2.0)
    # Next uses k = 2/(3+1) = 0.5.
    assert out[3] == pytest.approx(4 * 0.5 + 2.0 * 0.5)


def test_wma():
    # WMA of [1,2,3] period 3 = (1*1 + 2*2 + 3*3)/6 = 14/6.
    out = ind.wma([1, 2, 3], 3)
    assert out[2] == pytest.approx(14 / 6)


def test_rsi_all_gains_is_100():
    out = ind.rsi([i for i in range(1, 20)], 14)
    assert out[14] == pytest.approx(100.0)


def test_rsi_bounds():
    closes = [10, 11, 10.5, 11.5, 12, 11, 11.8, 12.5, 12.2, 13, 12.8, 13.5, 13.2, 14, 13.8, 14.5]
    out = ind.rsi(closes, 14)
    for v in out:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_macd_shapes():
    closes = [float(i) for i in range(1, 60)]
    m = ind.macd(closes)
    assert len(m["macd"]) == len(closes)
    assert len(m["signal"]) == len(closes)
    assert m["hist"][-1] is not None


def test_atr_positive():
    s = _series([float(i) for i in range(1, 40)])
    a = ind.atr(s, 14)
    assert a[-1] is not None and a[-1] > 0


def test_bollinger_contains_price_center():
    closes = [10.0] * 25
    b = ind.bollinger_bands(closes, 20, 2.0)
    # Flat series -> zero std -> bands collapse onto the mean.
    assert b["upper"][-1] == pytest.approx(10.0)
    assert b["lower"][-1] == pytest.approx(10.0)


def test_obv_direction():
    s = _series([10, 11, 10, 12], vols=[100, 200, 300, 400])
    o = ind.obv(s)
    assert o == [0.0, 200.0, -100.0, 300.0]


def test_roc():
    out = ind.roc([100, 110], 1)
    assert out[1] == pytest.approx(10.0)


def test_stochastic_bounds():
    s = _series([10, 12, 11, 13, 14, 12, 15, 16, 14, 17, 18, 16, 19, 20, 18, 21])
    st = ind.stochastic(s, 14, 3)
    for v in st["k"]:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_adx_defined_on_trend():
    s = _series([float(i) for i in range(1, 80)])
    a = ind.adx(s, 14)
    assert a["adx"][-1] is not None


def test_indicator_output_length_matches_input():
    closes = [float(i) for i in range(1, 50)]
    assert len(ind.ema(closes, 10)) == len(closes)
    assert len(ind.rsi(closes, 14)) == len(closes)
