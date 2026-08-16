"""Tests for structure detection (Group B, §4)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.levels import (classify_by_price, detect_channels, detect_gaps,
                          detect_levels, detect_trendlines, pivot_points,
                          volume_profile_levels)
from atlas.types import OHLCV, Bar


def _series(rows):
    """rows: list of (open, high, low, close, volume)."""
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [Bar(t0 + timedelta(days=i), *rows[i]) for i in range(len(rows))]
    return OHLCV.from_bars("TEST", "1d", bars)


def _from_closes(closes, vols=None):
    n = len(closes)
    vols = vols or [1000.0] * n
    return _series([(closes[i], closes[i] + 1, closes[i] - 1, closes[i], vols[i]) for i in range(n)])


UPTREND = _from_closes([100 + i + 3 * (i % 5) for i in range(120)])


# --- trendlines / channels ----------------------------------------------
def test_trendlines_rising_in_uptrend():
    tl = detect_trendlines(UPTREND, left=1, right=1)
    assert tl["support"] is not None
    assert tl["support"]["slope"] > 0
    assert tl["support"]["current_value"] > 0


def test_channel_reports_bounds():
    ch = detect_channels(UPTREND, left=1, right=1)
    if ch is not None:
        assert ch["upper"] >= ch["lower"]
        assert "parallel" in ch and "type" in ch


# --- pivot points --------------------------------------------------------
def test_pivot_points_classic_ordering():
    s = _series([(100, 110, 90, 105, 1000)])
    pp = pivot_points(s)
    c = pp["classic"]
    assert c["S3"] < c["S2"] < c["S1"] < c["P"] < c["R1"] < c["R2"] < c["R3"]
    # classic P = (H+L+C)/3 (values rounded to 4dp)
    assert c["P"] == pytest.approx((110 + 90 + 105) / 3, abs=1e-3)


def test_pivot_points_methods_present():
    s = _series([(100, 110, 90, 105, 1000)])
    pp = pivot_points(s)
    assert set(pp) >= {"classic", "camarilla", "woodie", "based_on"}
    assert "R4" in pp["camarilla"]


# --- gaps ----------------------------------------------------------------
def test_gap_up_detected_and_fill_status():
    # bar 2 gaps up above bar 1's high (10 -> low 12); bar 3 does not fill.
    rows = [
        (10, 10.5, 9.5, 10, 1000),
        (10, 10.6, 9.8, 10.2, 1000),
        (12, 12.5, 12.0, 12.3, 1000),   # gap up: low 12 > prev high 10.6
        (12.4, 12.9, 12.2, 12.6, 1000),
    ]
    s = _series(rows)
    gaps = detect_gaps(s, min_pct=0.5)
    assert any(g["type"] == "up" for g in gaps)
    up = [g for g in gaps if g["type"] == "up"][0]
    assert up["filled"] is False


def test_gap_filled_when_price_returns():
    rows = [
        (10, 10.5, 9.5, 10, 1000),
        (10, 10.6, 9.8, 10.2, 1000),
        (12, 12.5, 12.0, 12.3, 1000),   # gap up
        (11, 11.5, 10.0, 10.4, 1000),   # trades back down to/below prev high 10.6 -> filled
    ]
    s = _series(rows)
    up = [g for g in detect_gaps(s, min_pct=0.5) if g["type"] == "up"][0]
    assert up["filled"] is True


def test_no_gaps_on_smooth_series():
    assert detect_gaps(_from_closes([100 + i * 0.1 for i in range(50)]), min_pct=1.0) == []


# --- volume weighting & profile -----------------------------------------
def test_levels_have_strength_and_volume():
    lv = detect_levels(UPTREND, left=1, right=1)
    for entry in lv["support"] + lv["resistance"]:
        assert "strength" in entry and "volume" in entry


def test_volume_profile_levels_wraps_indicator():
    vp = volume_profile_levels(UPTREND, bins=10)
    assert vp is not None and "poc" in vp


def test_classify_by_price_still_works():
    cls = classify_by_price(UPTREND, left=1, right=1)
    close = cls["last_close"]
    assert all(x["price"] <= close for x in cls["support"])
    assert all(x["price"] > close for x in cls["resistance"])
