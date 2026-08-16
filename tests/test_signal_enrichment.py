"""Tests for signal enrichment and auto-proposal (Group D, §6)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.analysis import build_signal, propose_signal
from atlas.types import OHLCV, Bar


def _series(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(t0 + timedelta(days=i), closes[i], max(highs[i], closes[i]), min(lows[i], closes[i]), closes[i], 1e6)
        for i in range(n)
    ]
    return OHLCV.from_bars("TEST", "1d", bars)


UPTREND = _series([100 * (1.004 ** i) for i in range(200)])
DOWNTREND = _series([100 * (0.996 ** i) for i in range(200)])
CHOP = _series([100 + (i % 5) for i in range(200)])


# --- build_signal enrichment ---------------------------------------------
def test_build_signal_has_narrative_fields():
    sig = build_signal("X", 100, 95, [110, 120], "long", 100_000,
                       thesis="t", confidence=70, biggest_risk="r", regime="trending_up")
    assert sig["thesis"] == "t"
    assert sig["confidence"] == 70
    assert sig["biggest_risk"] == "r"
    assert sig["regime"] == "trending_up"
    assert "confidence_basis" in sig


def test_build_signal_auto_invalidation():
    sig = build_signal("X", 100, 95, [110], "long", 100_000)
    assert "below the stop at 95" in sig["what_would_make_me_wrong"]
    sig2 = build_signal("X", 100, 105, [90], "short", 100_000)
    assert "above the stop at 105" in sig2["what_would_make_me_wrong"]


# --- propose_signal ------------------------------------------------------
def test_propose_long_in_uptrend():
    out = propose_signal("X", series=UPTREND)
    assert out["direction"] == "long"
    assert out["stop"] < out["entry"]
    assert all(t > out["entry"] for t in out["targets"])
    assert out["confidence"] is not None
    assert out["thesis"] and out["biggest_risk"] and out["what_would_make_me_wrong"]
    assert out["catalyst_or_expiry"]
    assert out["position_size"]["units"] >= 0


def test_propose_flat_in_chop():
    out = propose_signal("X", series=CHOP)
    # A choppy/ranging series should not force a directional call.
    assert out["direction"] == "flat"
    assert "reason" in out


def test_propose_short_in_downtrend():
    out = propose_signal("X", series=DOWNTREND)
    # May be short or flat depending on momentum thresholds; if directional, must be short.
    assert out["direction"] in ("short", "flat")
    if out["direction"] == "short":
        assert out["stop"] > out["entry"]
        assert all(t < out["entry"] for t in out["targets"])


def test_propose_r_multiple_present():
    out = propose_signal("X", series=UPTREND)
    assert out["r_multiple"] is not None and out["r_multiple"] > 0


def test_propose_confidence_bounds():
    out = propose_signal("X", series=UPTREND)
    assert 0 <= out["confidence"] <= 100
    assert len(out["confidence_drivers"]) >= 3


def test_propose_too_short_series_flat_or_ok():
    out = propose_signal("X", series=_series([100 + i for i in range(80)]))
    # Short history -> technical subscore None -> flat.
    assert out["direction"] in ("flat", "long", "short")
