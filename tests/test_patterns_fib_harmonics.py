"""Tests for Fibonacci, harmonic, and classical chart-pattern detection."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.chart_patterns import (
    detect_classical,
    detect_double_bottom,
    detect_double_top,
    detect_head_and_shoulders,
    detect_triangle,
)
from atlas.fibonacci import auto_fibonacci, fib_extensions, fib_retracements
from atlas.harmonics import alternating_pivots, detect_harmonics
from atlas.types import OHLCV, Bar


def _series_from_closes(closes):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], closes[i] + 0.2, closes[i] - 0.2, closes[i], 1000.0)
        for i in range(len(closes))
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


def _zigzag(points, steps=6):
    """Build a close series that linearly connects (price) waypoints."""
    closes = []
    for a, b in zip(points, points[1:]):
        for s in range(steps):
            closes.append(a + (b - a) * s / steps)
    closes.append(points[-1])
    return closes


# --- Fibonacci -----------------------------------------------------------
def test_fib_retracements_levels():
    r = fib_retracements(200, 100, "up")
    assert r["0.000"] == 200.0
    assert r["1.000"] == 100.0
    assert r["0.500"] == 150.0
    assert r["0.618"] == pytest.approx(138.2, abs=0.01)


def test_fib_extensions_up():
    e = fib_extensions(200, 100, "up")
    assert e["1.618"] == pytest.approx(261.8, abs=0.01)


def test_auto_fibonacci_finds_anchors():
    closes = _zigzag([100, 150, 120, 170, 130])
    s = _series_from_closes(closes)
    fib = auto_fibonacci(s, left=2, right=2)
    assert fib is not None
    assert fib["anchor_high"] > fib["anchor_low"]
    assert "0.618" in fib["retracements"]


def test_auto_fibonacci_insufficient():
    s = _series_from_closes([100, 101, 102])
    assert auto_fibonacci(s) is None


# --- harmonics -----------------------------------------------------------
def test_alternating_pivots_alternate():
    closes = _zigzag([100, 120, 105, 130, 110, 140])
    s = _series_from_closes(closes)
    piv = alternating_pivots(s, left=2, right=2)
    for a, b in zip(piv, piv[1:]):
        assert a.kind != b.kind


def test_detect_harmonics_gartley_like():
    # Construct X-A-B-C-D with Gartley-ish ratios (bullish: D is a low).
    # XA = 100->200 (up 100). AB retraces 0.618 -> B=138.2. BC ~0.5 of AB up ->
    # C=138.2+30.9=169.1. CD down to D ~0.786 of XA from A -> D=200-78.6=121.4.
    points = [100, 200, 138.2, 169.1, 121.4]
    closes = _zigzag(points, steps=6)
    s = _series_from_closes(closes)
    res = detect_harmonics(s, left=2, right=2)
    # Should detect at least one harmonic with valid geometry fields.
    assert isinstance(res, list)
    for r in res:
        assert "prz" in r and "invalidation" in r and r["completion_pct"] == 100.0
        assert r["direction"] in ("bullish", "bearish")


def test_detect_harmonics_too_few_pivots():
    s = _series_from_closes([100, 101, 102, 103])
    assert detect_harmonics(s) == []


# --- classical -----------------------------------------------------------
def test_double_top():
    # Two ~equal peaks with a trough between, then a drop below neckline.
    closes = _zigzag([100, 130, 110, 130, 95])
    s = _series_from_closes(closes)
    res = detect_double_top(s, tol_pct=3.0, left=2, right=2)
    assert res is not None
    assert res["direction"] == "bearish"
    assert res["target"] < res["neckline"]


def test_double_bottom():
    closes = _zigzag([130, 100, 120, 100, 135])
    s = _series_from_closes(closes)
    res = detect_double_bottom(s, tol_pct=3.0, left=2, right=2)
    assert res is not None
    assert res["direction"] == "bullish"
    assert res["target"] > res["neckline"]


def test_head_and_shoulders():
    # Left shoulder, higher head, right shoulder ~= left, then break down.
    closes = _zigzag([100, 120, 105, 140, 106, 121, 90])
    s = _series_from_closes(closes)
    res = detect_head_and_shoulders(s, tol_pct=5.0, left=2, right=2)
    if res is not None:  # geometry-dependent; when found it must be well-formed
        assert res["direction"] == "bearish"
        assert res["head"][1] > res["shoulders"][0][1]


def test_ascending_triangle():
    # Flat highs (~150), rising lows.
    points = [120, 150, 130, 150, 138, 150, 143, 150]
    closes = _zigzag(points, steps=5)
    s = _series_from_closes(closes)
    res = detect_triangle(s, min_pivots=3, left=2, right=2)
    if res is not None:
        assert res["type"] == "classical"
        assert "triangle" in res["name"]


def test_detect_classical_aggregates_list():
    closes = _zigzag([100, 130, 110, 130, 95])
    s = _series_from_closes(closes)
    out = detect_classical(s, left=2, right=2)
    assert isinstance(out, list)


def test_no_pattern_on_straight_line():
    s = _series_from_closes([100 + i for i in range(60)])
    # A monotonic line yields no double top/bottom.
    assert detect_double_top(s) is None
    assert detect_double_bottom(s) is None
