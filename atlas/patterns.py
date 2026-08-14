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


def detect_patterns(series: OHLCV, families: Optional[List[str]] = None) -> List[dict]:
    """Scan the series for candlestick patterns. ``families`` currently accepts
    ``"candlestick"``; unknown families are ignored with no fabrication."""
    out: List[Pattern] = []
    n = len(series)
    o, h, l, c = series.open, series.high, series.low, series.close
    for i in range(n):
        rng = _range(h[i], l[i])
        body = _body(o[i], c[i])
        upper_wick = h[i] - max(o[i], c[i])
        lower_wick = min(o[i], c[i]) - l[i]

        # Doji: very small body relative to range.
        if body <= 0.1 * rng:
            out.append(Pattern("doji", i, "neutral", 1.0 - body / rng))

        # Hammer: small body up top, long lower wick, little upper wick.
        if lower_wick >= 2 * body and upper_wick <= body and body > 0:
            out.append(Pattern("hammer", i, "bullish", min(1.0, lower_wick / rng)))

        # Shooting star: small body down low, long upper wick.
        if upper_wick >= 2 * body and lower_wick <= body and body > 0:
            out.append(Pattern("shooting_star", i, "bearish", min(1.0, upper_wick / rng)))

        # Marubozu: body fills almost the whole range.
        if body >= 0.95 * rng:
            direction = "bullish" if c[i] > o[i] else "bearish"
            out.append(Pattern("marubozu", i, direction, body / rng))

        # Two-bar engulfing patterns.
        if i >= 1:
            prev_body = _body(o[i - 1], c[i - 1])
            bull = (
                c[i - 1] < o[i - 1]           # prior bearish
                and c[i] > o[i]               # current bullish
                and c[i] >= o[i - 1]
                and o[i] <= c[i - 1]
                and body > prev_body
            )
            bear = (
                c[i - 1] > o[i - 1]           # prior bullish
                and c[i] < o[i]               # current bearish
                and o[i] >= c[i - 1]
                and c[i] <= o[i - 1]
                and body > prev_body
            )
            if bull:
                out.append(Pattern("bullish_engulfing", i, "bullish", min(1.0, body / _range(h[i], l[i]))))
            if bear:
                out.append(Pattern("bearish_engulfing", i, "bearish", min(1.0, body / _range(h[i], l[i]))))

    return [p.to_dict() for p in out]


def latest_patterns(series: OHLCV, lookback: int = 5) -> List[dict]:
    """Patterns whose signal bar falls within the last ``lookback`` bars."""
    all_p = detect_patterns(series)
    cutoff = len(series) - lookback
    return [p for p in all_p if p["index"] >= cutoff]
