"""Candlestick-pattern recognition (subset of Section 5).

Only patterns that are *honestly computable* from OHLC geometry are implemented.
Each detection reports the bar index, direction, and a geometric confidence in
[0, 1]. Base rates are left ``None`` unless a historical study supplies them —
the spec forbids inventing follow-through statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .types import OHLCV


@dataclass
class Pattern:
    name: str
    index: int
    direction: str          # "bullish" | "bearish" | "neutral"
    confidence: float       # geometric strength in [0, 1]
    base_rate: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "index": self.index,
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "base_rate": self.base_rate,
        }


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _sma(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


def detect_patterns(series: OHLCV, families: Optional[List[str]] = None) -> List[dict]:
    """Scan the series for candlestick patterns. ``families`` currently accepts
    ``"candlestick"``; unknown families are ignored with no fabrication."""
    out: List[Pattern] = []
    n = len(series)
    o, h, l, c = series.open, series.high, series.low, series.close
    trend = _sma(list(c), 10)  # simple trend context for hammer/hanging-man

    def uptrend(i):
        return trend[i] is not None and c[i] > trend[i]

    for i in range(n):
        rng = _range(h[i], l[i])
        body = _body(o[i], c[i])
        upper_wick = h[i] - max(o[i], c[i])
        lower_wick = min(o[i], c[i]) - l[i]

        # Doji: very small body relative to range.
        if body <= 0.1 * rng:
            out.append(Pattern("doji", i, "neutral", 1.0 - body / rng))

        # Hammer vs hanging man: same geometry, trend decides the read.
        if lower_wick >= 2 * body and upper_wick <= body and body > 0:
            if uptrend(i):
                out.append(Pattern("hanging_man", i, "bearish", min(1.0, lower_wick / rng)))
            else:
                out.append(Pattern("hammer", i, "bullish", min(1.0, lower_wick / rng)))

        # Shooting star / inverted hammer: long upper wick, small body.
        if upper_wick >= 2 * body and lower_wick <= body and body > 0:
            if uptrend(i):
                out.append(Pattern("shooting_star", i, "bearish", min(1.0, upper_wick / rng)))
            else:
                out.append(Pattern("inverted_hammer", i, "bullish", min(1.0, upper_wick / rng)))

        # Marubozu: body fills almost the whole range.
        if body >= 0.95 * rng:
            direction = "bullish" if c[i] > o[i] else "bearish"
            out.append(Pattern("marubozu", i, direction, body / rng))

        # Two-bar patterns.
        if i >= 1:
            prev_body = _body(o[i - 1], c[i - 1])
            top_i, bot_i = max(o[i], c[i]), min(o[i], c[i])
            top_p, bot_p = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])

            # Engulfing.
            if c[i - 1] < o[i - 1] and c[i] > o[i] and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body > prev_body:
                out.append(Pattern("bullish_engulfing", i, "bullish", min(1.0, body / rng)))
            if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body > prev_body:
                out.append(Pattern("bearish_engulfing", i, "bearish", min(1.0, body / rng)))

            # Harami: small current body contained within the prior (large) body.
            if prev_body > 0 and body < 0.6 * prev_body and top_i <= top_p and bot_i >= bot_p:
                if c[i - 1] < o[i - 1] and c[i] > o[i]:
                    out.append(Pattern("bullish_harami", i, "bullish", 1.0 - body / prev_body))
                elif c[i - 1] > o[i - 1] and c[i] < o[i]:
                    out.append(Pattern("bearish_harami", i, "bearish", 1.0 - body / prev_body))

            # Tweezers: matching highs (top) or lows (bottom).
            if abs(h[i] - h[i - 1]) / max(h[i], 1e-9) < 0.0015 and uptrend(i):
                out.append(Pattern("tweezer_top", i, "bearish", 0.6))
            if abs(l[i] - l[i - 1]) / max(l[i], 1e-9) < 0.0015 and not uptrend(i):
                out.append(Pattern("tweezer_bottom", i, "bullish", 0.6))

        # Three-bar patterns.
        if i >= 2:
            b1 = _body(o[i - 2], c[i - 2])
            star = _body(o[i - 1], c[i - 1])
            mid1 = (o[i - 2] + c[i - 2]) / 2

            # Morning / evening star.
            if b1 > 0 and star < 0.4 * b1:
                if c[i - 2] < o[i - 2] and c[i] > o[i] and c[i] > mid1:
                    out.append(Pattern("morning_star", i, "bullish", min(1.0, body / rng)))
                if c[i - 2] > o[i - 2] and c[i] < o[i] and c[i] < mid1:
                    out.append(Pattern("evening_star", i, "bearish", min(1.0, body / rng)))

            # Three white soldiers / black crows.
            bodies_ok = all(_body(o[k], c[k]) >= 0.5 * _range(h[k], l[k]) for k in (i - 2, i - 1, i))
            if bodies_ok and all(c[k] > o[k] for k in (i - 2, i - 1, i)) and c[i] > c[i - 1] > c[i - 2]:
                out.append(Pattern("three_white_soldiers", i, "bullish", 0.8))
            if bodies_ok and all(c[k] < o[k] for k in (i - 2, i - 1, i)) and c[i] < c[i - 1] < c[i - 2]:
                out.append(Pattern("three_black_crows", i, "bearish", 0.8))

    return [p.to_dict() for p in out]


def latest_patterns(series: OHLCV, lookback: int = 5) -> List[dict]:
    """Patterns whose signal bar falls within the last ``lookback`` bars."""
    all_p = detect_patterns(series)
    cutoff = len(series) - lookback
    return [p for p in all_p if p["index"] >= cutoff]


def pattern_base_rate(series: OHLCV, pattern_name: str, forward: int = 10) -> Optional[dict]:
    """Empirical follow-through rate for a candlestick pattern *on this series*.

    Finds every past occurrence of ``pattern_name`` with at least ``forward``
    bars of data after it, measures the forward return, and reports how often
    price moved in the pattern's direction. This is a real, in-sample study of
    the given data — not a fabricated statistic — and is labelled as such. A
    small sample means little; the sample size is always returned.
    """
    occ = [p for p in detect_patterns(series) if p["name"] == pattern_name]
    hits = 0
    rets: List[float] = []
    for p in occ:
        i = p["index"]
        j = i + forward
        if j >= len(series) or series.close[i] <= 0:
            continue
        ret = (series.close[j] / series.close[i] - 1.0) * 100.0
        rets.append(ret)
        if p["direction"] == "bullish" and ret > 0:
            hits += 1
        elif p["direction"] == "bearish" and ret < 0:
            hits += 1
        elif p["direction"] == "neutral":
            hits += 0  # neutral patterns have no directional expectation
    n = len(rets)
    if n == 0:
        return None
    return {
        "pattern": pattern_name,
        "sample_size": n,
        "forward_bars": forward,
        "follow_through_rate": round(hits / n, 3),
        "avg_forward_return_pct": round(sum(rets) / n, 2),
        "basis": "in-sample empirical study on this series (not a universal base rate)",
        "reliable": n >= 20,
    }
