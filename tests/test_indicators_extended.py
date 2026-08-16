"""Tests for the extended indicator library (Group A)."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from atlas import indicators as ind
from atlas.types import OHLCV, Bar


def _series(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    vols = vols or [1000.0 + 10 * i for i in range(n)]
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], max(highs[i], closes[i]), min(lows[i], closes[i]), closes[i], vols[i])
        for i in range(n)
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


UP = [float(i) for i in range(1, 80)]
OSC = [10 + 5 * math.sin(i / 3) for i in range(80)]


def _len_ok(seq, n):
    return len(seq) == n


# --- trend ---------------------------------------------------------------
def test_hma_length_and_trend():
    out = ind.hma(UP, 16)
    assert _len_ok(out, len(UP))
    assert out[-1] is not None and out[-1] > out[-5]


def test_vwma_between_extremes():
    s = _series(UP)
    out = ind.vwma(s, 10)
    assert out[-1] is not None and min(UP) <= out[-1] <= max(UP)


def test_anchored_vwap_starts_at_anchor():
    s = _series(UP)
    out = ind.anchored_vwap(s, anchor_index=10)
    assert out[9] is None and out[10] is not None


def test_supertrend_direction_up_in_uptrend():
    s = _series(UP)
    st = ind.supertrend(s, period=10, multiplier=3)
    assert st["direction"][-1] == 1
    assert st["supertrend"][-1] is not None


def test_ichimoku_lines_defined():
    s = _series(UP)
    ich = ind.ichimoku(s)
    assert ich["tenkan"][-1] is not None and ich["kijun"][-1] is not None
    assert len(ich["chikou"]) == len(s)


def test_parabolic_sar_length():
    s = _series(OSC)
    sar = ind.parabolic_sar(s)
    assert _len_ok(sar, len(s)) and sar[-1] is not None


def test_linreg_channel_slope_positive_uptrend():
    out = ind.linreg_channel(UP, 20)
    assert out["slope"][-1] is not None and out["slope"][-1] > 0
    # Perfect line -> zero residual band (upper == mid == lower); use <= .
    assert out["lower"][-1] <= out["mid"][-1] <= out["upper"][-1]
    # An oscillating series should produce a non-degenerate band.
    osc = ind.linreg_channel(OSC, 20)
    assert osc["lower"][-1] < osc["upper"][-1]


# --- momentum ------------------------------------------------------------
def test_stoch_rsi_bounds():
    out = ind.stoch_rsi(OSC)
    for v in out["k"]:
        if v is not None:
            assert -0.001 <= v <= 100.001


def test_cci_defined():
    s = _series(OSC)
    out = ind.cci(s, 20)
    assert out[-1] is not None


def test_williams_r_bounds():
    s = _series(OSC)
    out = ind.williams_r(s, 14)
    for v in out:
        if v is not None:
            assert -100.001 <= v <= 0.001


def test_tsi_sign_in_uptrend():
    out = ind.tsi(UP)
    assert out[-1] is not None and out[-1] > 0


def test_mfi_bounds():
    s = _series(OSC)
    out = ind.mfi(s, 14)
    for v in out:
        if v is not None:
            assert 0 <= v <= 100


def test_rsi_divergence_returns_list():
    s = _series(OSC)
    out = ind.rsi_divergence(s, left=2, right=2)
    assert isinstance(out, list)
    for d in out:
        assert d["type"] in ("bullish", "bearish")


# --- volatility ----------------------------------------------------------
def test_keltner_ordering():
    s = _series(OSC)
    k = ind.keltner_channels(s)
    i = len(s) - 1
    assert k["lower"][i] < k["middle"][i] < k["upper"][i]


def test_donchian_contains_price():
    s = _series(OSC)
    d = ind.donchian_channels(s, 20)
    i = len(s) - 1
    assert d["lower"][i] <= s.close[i] <= d["upper"][i]


def test_historical_volatility_positive():
    out = ind.historical_volatility(OSC, 20)
    assert out[-1] is not None and out[-1] > 0


def test_choppiness_bounds():
    s = _series(OSC)
    out = ind.choppiness_index(s, 14)
    for v in out:
        if v is not None:
            assert 0 <= v <= 100.5


# --- volume --------------------------------------------------------------
def test_ad_line_length():
    s = _series(UP)
    out = ind.ad_line(s)
    assert _len_ok(out, len(s)) and out[-1] is not None


def test_cmf_bounds():
    s = _series(OSC)
    out = ind.cmf(s, 20)
    for v in out:
        if v is not None:
            assert -1.001 <= v <= 1.001


def test_vwap_bands_ordering():
    s = _series(OSC)
    b = ind.vwap_bands(s)
    i = len(s) - 1
    assert b["lower"][i] <= b["vwap"][i] <= b["upper"][i]


def test_volume_profile_poc_in_range():
    s = _series(OSC)
    vp = ind.volume_profile(s, bins=10)
    assert vp is not None
    assert min(s.low) <= vp["poc"] <= max(s.high)
    assert vp["value_area_low"] <= vp["poc"] <= vp["value_area_high"]


# --- relative ------------------------------------------------------------
def test_beta_and_correlation_self_is_one():
    s = _series(OSC)
    assert ind.beta(s, s) == pytest.approx(1.0, abs=1e-6)
    assert ind.correlation(s, s) == pytest.approx(1.0, abs=1e-6)


def test_rs_rating_outperformer_high():
    strong = _series([100 * (1.02 ** i) for i in range(260)])
    weak = _series([100 * (1.001 ** i) for i in range(260)])
    r = ind.rs_rating(strong, weak)
    assert r is not None and r > 60


def test_rs_rating_insufficient_history_none():
    a = _series([float(i) for i in range(1, 50)])  # 49 bars < shortest period (63)
    assert ind.rs_rating(a, a) is None
