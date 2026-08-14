"""Structure detection: support/resistance, swings, pivots (Section 4).

Levels are derived mechanically from swing pivots and touch-counts. Nothing is
drawn from imagination — a level exists only where the price data supports it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .types import OHLCV


@dataclass
class Level:
    price: float
    kind: str          # "support" | "resistance"
    touches: int
    last_touch_index: int

    def to_dict(self) -> dict:
        return {
            "price": round(self.price, 4),
            "kind": self.kind,
            "touches": self.touches,
            "last_touch_index": self.last_touch_index,
        }


def swing_points(series: OHLCV, left: int = 2, right: int = 2):
    """Fractal swing highs/lows: a bar higher/lower than ``left``/``right`` neighbours."""
    highs: List[int] = []
    lows: List[int] = []
    n = len(series)
    for i in range(left, n - right):
        window_h = series.high[i - left : i + right + 1]
        window_l = series.low[i - left : i + right + 1]
        if series.high[i] == max(window_h) and series.high[i] > series.high[i - 1]:
            highs.append(i)
        if series.low[i] == min(window_l) and series.low[i] < series.low[i - 1]:
            lows.append(i)
    return {"highs": highs, "lows": lows}


def detect_levels(series: OHLCV, left: int = 2, right: int = 2, tolerance_pct: float = 0.5) -> dict:
    """Cluster swing pivots into horizontal support/resistance levels.

    Pivots within ``tolerance_pct`` of each other are merged; ``touches`` counts
    how many pivots formed the level (a proxy for its strength).
    """
    swings = swing_points(series, left, right)
    resistances = _cluster(series.high, swings["highs"], tolerance_pct, "resistance")
    supports = _cluster(series.low, swings["lows"], tolerance_pct, "support")
    last = len(series) - 1
    return {
        "support": [l.to_dict() for l in sorted(supports, key=lambda x: -x.price)],
        "resistance": [l.to_dict() for l in sorted(resistances, key=lambda x: x.price)],
        "recent_high": max(series.high) if len(series) else None,
        "recent_low": min(series.low) if len(series) else None,
        "last_close": series.close[last] if len(series) else None,
    }


def _cluster(prices, indices: List[int], tolerance_pct: float, kind: str) -> List[Level]:
    levels: List[Level] = []
    for idx in indices:
        p = prices[idx]
        merged = False
        for lv in levels:
            if lv.price and abs(p - lv.price) / lv.price * 100.0 <= tolerance_pct:
                # Weighted-average the level price and bump the touch count.
                total = lv.touches + 1
                lv.price = (lv.price * lv.touches + p) / total
                lv.touches = total
                lv.last_touch_index = max(lv.last_touch_index, idx)
                merged = True
                break
        if not merged:
            levels.append(Level(price=p, kind=kind, touches=1, last_touch_index=idx))
    return levels


def nearest_levels(series: OHLCV, **kwargs) -> dict:
    """Nearest support below and resistance above the last close."""
    lv = detect_levels(series, **kwargs)
    close = lv["last_close"]
    if close is None:
        return {"support_below": None, "resistance_above": None}
    below = [s for s in lv["support"] if s["price"] <= close]
    above = [r for r in lv["resistance"] if r["price"] >= close]
    return {
        "support_below": max(below, key=lambda x: x["price"]) if below else None,
        "resistance_above": min(above, key=lambda x: x["price"]) if above else None,
    }
