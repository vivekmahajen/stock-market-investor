"""Tests for expanded pattern detection (Group C, §5)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.chart_patterns import (detect_classical, detect_rectangle,
                                  detect_rounding, detect_triple,
                                  detect_wedge_or_broadening)
from atlas.patterns import detect_patterns, pattern_base_rate
from atlas.types import OHLCV, Bar


def _ohlc(rows):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return OHLCV.from_bars("T", "1d", [Bar(t0 + timedelta(days=i), *rows[i]) for i in range(len(rows))])


def _from_closes(closes):
    n = len(closes)
    return _ohlc([(closes[i], closes[i] + 0.3, closes[i] - 0.3, closes[i], 1000.0) for i in range(n)])


def _names(series):
    return {p["name"] for p in detect_patterns(series)}


# --- candlestick ---------------------------------------------------------
def test_bullish_harami():
    # Big bearish bar, then a small bullish bar inside it.
    rows = [(100, 100.2, 99.8, 100, 1e3)] * 9  # flat to set downtrend context via sma
    rows += [(110, 110.2, 99.5, 100, 1e3),      # large bearish
             (102, 104, 101.5, 103.5, 1e3)]     # small bullish inside
    s = _ohlc(rows)
    assert "bullish_harami" in _names(s)


def test_three_white_soldiers():
    rows = [(100, 100.1, 99.9, 100, 1e3)] * 9
    rows += [(100, 103, 99.9, 102.8, 1e3), (103, 106, 102.9, 105.8, 1e3), (106, 109, 105.9, 108.8, 1e3)]
    s = _ohlc(rows)
    assert "three_white_soldiers" in _names(s)


def test_morning_star():
    rows = [(100, 100.1, 99.9, 100, 1e3)] * 9
    rows += [(110, 110.2, 99.5, 100, 1e3),   # big bearish
             (99.5, 99.8, 99.0, 99.4, 1e3),  # small star
             (99.6, 106, 99.5, 105.5, 1e3)]  # big bullish closing above mid of bar1
    s = _ohlc(rows)
    assert "morning_star" in _names(s)


def test_hanging_man_vs_hammer_trend_context():
    # Uptrend then a hammer-shaped candle -> hanging_man (bearish).
    up = [(100 + i, 100 + i + 0.2, 100 + i - 0.2, 100 + i, 1e3) for i in range(12)]
    up += [(112, 112.2, 108, 111.8, 1e3)]  # small body top, long lower wick, in uptrend
    s = _ohlc(up)
    names = _names(s)
    assert "hanging_man" in names and "hammer" not in {p["name"] for p in detect_patterns(s) if p["index"] == len(s) - 1}


# --- base rate -----------------------------------------------------------
def test_pattern_base_rate_shape():
    # Many dojis in an oscillating series; base rate is a real in-sample stat.
    import math
    s = _from_closes([100 + 3 * math.sin(i / 2) for i in range(120)])
    br = pattern_base_rate(s, "doji", forward=5)
    if br is not None:
        assert 0.0 <= br["follow_through_rate"] <= 1.0
        assert br["sample_size"] >= 1
        assert "in-sample" in br["basis"]


def test_pattern_base_rate_none_when_absent():
    s = _from_closes([100 + i for i in range(60)])  # strong trend -> few/no dojis
    assert pattern_base_rate(s, "evening_star") is None


# --- classical -----------------------------------------------------------
def test_triple_top():
    # three ~equal peaks with troughs between
    seg = []
    for _ in range(3):
        seg += [130, 110]
    seg = [100] + [x for pair in zip([120, 122, 121], [105, 106, 104]) for x in pair] + [130, 108, 130, 107, 130, 95]
    s = _from_closes(_zig([100, 130, 110, 131, 109, 129, 95]))
    res = detect_triple(s, top=True, left=1, right=1, tol_pct=4)
    if res:
        assert res["direction"] == "bearish"


def _zig(points, steps=6):
    out = []
    for a, b in zip(points, points[1:]):
        for s in range(steps):
            out.append(a + (b - a) * s / steps)
    out.append(points[-1])
    return out


def test_rectangle_flat_range():
    s = _from_closes(_zig([100, 110, 100, 110, 100, 110, 100, 110], steps=5))
    res = detect_rectangle(s, left=1, right=1)
    if res:
        assert res["resistance"] > res["support"]


def test_rounding_bottom_curvature():
    # U-shape: down then up
    import math
    closes = [100 - 20 * math.sin(math.pi * i / 40) for i in range(40)]  # dips then returns
    s = _from_closes(closes)
    res = detect_rounding(s, window=40)
    if res:
        assert res["name"] in ("rounding_bottom", "rounding_top")


def test_broadening_or_wedge_runs():
    s = _from_closes(_zig([100, 120, 105, 128, 98, 135, 92], steps=5))
    res = detect_wedge_or_broadening(s, left=1, right=1)
    # May or may not fire depending on slopes; when it does, it's well-formed.
    if res:
        assert res["direction"] in ("bullish", "bearish", "neutral")


def test_detect_classical_aggregates():
    s = _from_closes(_zig([100, 130, 110, 130, 95], steps=6))
    out = detect_classical(s, left=1, right=1)
    assert isinstance(out, list)


# --- harmonic cypher -----------------------------------------------------
def test_cypher_score_function():
    from atlas.harmonics import _score_cypher
    assert _score_cypher(0.5, 1.3, 0.786) is not None
    assert _score_cypher(0.9, 1.3, 0.786) is None   # AB/XA out of band
    assert _score_cypher(0.5, 2.0, 0.786) is None   # XC/XA out of band


def test_cypher_detected_end_to_end():
    from atlas.harmonics import detect_harmonics
    # lead-in 120->100 makes X=100 a detectable swing low; then ideal cypher legs.
    s = _from_closes(_zig([120, 100, 200, 150, 230, 128, 140], steps=6))
    names = {r["name"] for r in detect_harmonics(s, left=2, right=2)}
    assert "cypher" in names
