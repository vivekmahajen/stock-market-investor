"""Tests for options/greeks, the levels fix, and text report rendering."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas import bs_greeks, bs_price, implied_vol, option_analysis
from atlas.levels import classify_by_price
from atlas.options import CALL, PUT
from atlas.report import format_analysis, format_option
from atlas.types import OHLCV, Bar


# --- options -------------------------------------------------------------
def test_put_call_parity():
    S, K, T, r, sigma = 100, 100, 1.0, 0.05, 0.2
    c = bs_price(S, K, T, r, sigma, CALL)
    p = bs_price(S, K, T, r, sigma, PUT)
    # c - p = S - K e^{-rT}
    import math
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)


def test_atm_call_known_value():
    # Textbook: S=K=100, T=1, r=0.05, sigma=0.2 -> call ~= 10.4506
    assert bs_price(100, 100, 1.0, 0.05, 0.2, CALL) == pytest.approx(10.4506, abs=1e-3)


def test_call_delta_bounds():
    d = bs_greeks(100, 100, 1.0, 0.05, 0.2, CALL)["delta"]
    assert 0.0 < d < 1.0
    dp = bs_greeks(100, 100, 1.0, 0.05, 0.2, PUT)["delta"]
    assert -1.0 < dp < 0.0


def test_gamma_vega_positive():
    g = bs_greeks(100, 100, 0.5, 0.03, 0.25, CALL)
    assert g["gamma"] > 0 and g["vega"] > 0
    assert g["theta"] < 0  # long option bleeds time value


def test_implied_vol_roundtrip():
    price = bs_price(100, 105, 0.5, 0.04, 0.30, CALL)
    iv = implied_vol(price, 100, 105, 0.5, 0.04, CALL)
    assert iv == pytest.approx(0.30, abs=1e-4)


def test_implied_vol_out_of_bounds():
    # Price above the theoretical max -> None.
    assert implied_vol(1000, 100, 100, 1.0, 0.05, CALL) is None


def test_expiry_intrinsic_value():
    assert bs_price(110, 100, 0.0, 0.05, 0.2, CALL) == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, 0.05, 0.2, PUT) == pytest.approx(10.0)


def test_option_analysis_price_mode():
    out = option_analysis(100, 100, 0.25, 0.04, CALL, sigma=0.2)
    assert out["price"] > 0 and out["moneyness"] == "ATM"
    assert set(out["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}


def test_option_analysis_iv_mode():
    px = bs_price(100, 95, 0.3, 0.04, 0.28, CALL)
    out = option_analysis(100, 95, 0.3, 0.04, CALL, price=px)
    assert out["implied_vol"] == pytest.approx(0.28, abs=1e-3)


def test_option_invalid_inputs():
    with pytest.raises(ValueError):
        bs_price(-1, 100, 1, 0.05, 0.2, CALL)
    with pytest.raises(ValueError):
        option_analysis(100, 100, 1, 0.05, CALL)  # neither sigma nor price


# --- levels fix ----------------------------------------------------------
def _uptrend(n=120):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    for i in range(n):
        price *= 1.01 if i % 7 else 0.985  # rising with periodic dips (swing lows)
        bars.append(Bar(t0 + timedelta(days=i), price, price * 1.01, price * 0.99, price, 1000.0))
    return OHLCV.from_bars("UP", "1d", bars)


def test_classify_by_price_support_below_resistance_above():
    s = _uptrend()
    cls = classify_by_price(s, left=1, right=1)
    close = cls["last_close"]
    assert all(x["price"] <= close for x in cls["support"])
    assert all(x["price"] > close for x in cls["resistance"])
    # nearest support is the highest one below price (first in the list)
    if len(cls["support"]) > 1:
        assert cls["support"][0]["price"] >= cls["support"][1]["price"]


def test_classify_by_price_has_distance():
    s = _uptrend()
    cls = classify_by_price(s, left=1, right=1)
    for x in cls["support"] + cls["resistance"]:
        assert "distance_pct" in x


# --- report --------------------------------------------------------------
def test_format_option_readable():
    out = option_analysis(100, 100, 0.25, 0.04, CALL, sigma=0.2)
    text = format_option(out)
    assert "CALL" in text and "delta" in text and "price" in text


def test_format_analysis_headline_and_sections():
    out = {
        "symbol": "MSFT", "atlas_score": 81.4, "score_label": "buy", "regime": "trending_up",
        "asof": "2026-08-14", "score_horizon": "4-8w", "data_is_simulated": False,
        "subscores": {"technical": 90, "fundamental": 79, "sentiment": 70, "relative_strength": 93, "risk": 64},
        "confluence": {"score": 97.8}, "top_contributors": ["+ technical (90)"],
        "levels": {"last_close": 495.4, "nearest_support": {"price": 491.5, "touches": 1},
                   "nearest_resistance": {"price": 513.7, "touches": 1},
                   "support": [491.5], "resistance": [513.7]},
        "patterns": {"candlestick": [{"name": "doji", "direction": "neutral"}], "classical": [], "harmonic": []},
        "events": [{"type": "earnings", "date": "2026-08-20", "days_away": 6, "risk": "high"}],
        "notes": ["something"], "disclaimer": "Educational analysis, not financial advice.",
    }
    text = format_analysis(out)
    assert "MSFT" in text and "ATLAS 81.4" in text and "BUY" in text
    assert "SUB-SCORES" in text and "EVENT RISK" in text
    assert "Educational analysis" in text


def test_format_analysis_error():
    assert "ERROR" in format_analysis({"symbol": "X", "error": "boom"})
