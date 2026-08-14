"""Classical chart-pattern detection (Section 5).

Double tops/bottoms, head-and-shoulders (and inverse), and triangles — derived
from swing pivots and reported with a measured target, an invalidation level,
and a completion state (forming vs. confirmed by a neckline break). Nothing is
asserted to "will" happen; every pattern is conditional geometry.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .levels import swing_points
from .types import OHLCV


def _slope(indices: List[int], values: List[float]) -> float:
    """Least-squares slope of values vs. indices (per bar)."""
    n = len(indices)
    if n < 2:
        return 0.0
    mx = sum(indices) / n
    my = sum(values) / n
    denom = sum((x - mx) ** 2 for x in indices)
    if denom == 0:
        return 0.0
    return sum((indices[i] - mx) * (values[i] - my) for i in range(n)) / denom


def detect_double_top(series: OHLCV, tol_pct: float = 2.0, left: int = 2, right: int = 2) -> Optional[dict]:
    sw = swing_points(series, left, right)
    highs, lows = sw["highs"], sw["lows"]
    if len(highs) < 2:
        return None
    i1, i2 = highs[-2], highs[-1]
    p1, p2 = series.high[i1], series.high[i2]
    if abs(p1 - p2) / max(p1, p2) * 100.0 > tol_pct:
        return None
    troughs = [lows_i for lows_i in lows if i1 < lows_i < i2]
    if not troughs:
        return None
    neckline = min(series.low[t] for t in troughs)
    peak = (p1 + p2) / 2
    height = peak - neckline
    if height <= 0:
        return None
    last_close = series.close[-1]
    confirmed = last_close < neckline
    return {
        "name": "double_top", "type": "classical", "direction": "bearish",
        "peaks": [[i1, round(p1, 4)], [i2, round(p2, 4)]],
        "neckline": round(neckline, 4),
        "target": round(neckline - height, 4),
        "invalidation": round(max(p1, p2), 4),
        "completion_pct": 100.0 if confirmed else round(_progress(last_close, peak, neckline), 1),
        "confirmed": confirmed, "base_rate": None,
    }


def detect_double_bottom(series: OHLCV, tol_pct: float = 2.0, left: int = 2, right: int = 2) -> Optional[dict]:
    sw = swing_points(series, left, right)
    highs, lows = sw["highs"], sw["lows"]
    if len(lows) < 2:
        return None
    i1, i2 = lows[-2], lows[-1]
    p1, p2 = series.low[i1], series.low[i2]
    if abs(p1 - p2) / max(p1, p2) * 100.0 > tol_pct:
        return None
    peaks = [h for h in highs if i1 < h < i2]
    if not peaks:
        return None
    neckline = max(series.high[h] for h in peaks)
    trough = (p1 + p2) / 2
    height = neckline - trough
    if height <= 0:
        return None
    last_close = series.close[-1]
    confirmed = last_close > neckline
    return {
        "name": "double_bottom", "type": "classical", "direction": "bullish",
        "troughs": [[i1, round(p1, 4)], [i2, round(p2, 4)]],
        "neckline": round(neckline, 4),
        "target": round(neckline + height, 4),
        "invalidation": round(min(p1, p2), 4),
        "completion_pct": 100.0 if confirmed else round(_progress(last_close, trough, neckline), 1),
        "confirmed": confirmed, "base_rate": None,
    }


def detect_head_and_shoulders(series: OHLCV, tol_pct: float = 3.0, left: int = 2, right: int = 2,
                              inverse: bool = False) -> Optional[dict]:
    sw = swing_points(series, left, right)
    pivots = sw["lows"] if inverse else sw["highs"]
    prices = series.low if inverse else series.high
    if len(pivots) < 3:
        return None
    l, h, r = pivots[-3], pivots[-2], pivots[-1]
    ls, head, rs = prices[l], prices[h], prices[r]
    # Head must be the extreme; shoulders roughly symmetric.
    if inverse:
        if not (head < ls and head < rs):
            return None
    else:
        if not (head > ls and head > rs):
            return None
    if abs(ls - rs) / max(ls, rs) * 100.0 > tol_pct:
        return None

    # Neckline through the two intervening opposite pivots.
    opp = sw["highs"] if inverse else sw["lows"]
    opp_prices = series.high if inverse else series.low
    between = [o for o in opp if l < o < r]
    if len(between) < 2:
        return None
    neckline = sum(opp_prices[o] for o in between) / len(between)
    height = abs(head - neckline)
    last_close = series.close[-1]
    if inverse:
        confirmed = last_close > neckline
        target = neckline + height
        direction = "bullish"
    else:
        confirmed = last_close < neckline
        target = neckline - height
        direction = "bearish"
    return {
        "name": "inverse_head_and_shoulders" if inverse else "head_and_shoulders",
        "type": "classical", "direction": direction,
        "shoulders": [[l, round(ls, 4)], [r, round(rs, 4)]],
        "head": [h, round(head, 4)],
        "neckline": round(neckline, 4),
        "target": round(target, 4),
        "invalidation": round(head, 4),
        "completion_pct": 100.0 if confirmed else round(_progress(last_close, head, neckline), 1),
        "confirmed": confirmed, "base_rate": None,
    }


def detect_triangle(series: OHLCV, min_pivots: int = 3, left: int = 2, right: int = 2,
                    flat_slope: float = 1e-4) -> Optional[dict]:
    sw = swing_points(series, left, right)
    highs, lows = sw["highs"], sw["lows"]
    if len(highs) < min_pivots or len(lows) < min_pivots:
        return None
    hi_idx = highs[-min_pivots:]
    lo_idx = lows[-min_pivots:]
    hi_slope = _slope(hi_idx, [series.high[i] for i in hi_idx])
    lo_slope = _slope(lo_idx, [series.low[i] for i in lo_idx])

    # Normalise slopes by price scale to classify flat/rising/falling.
    scale = series.close[-1] or 1.0
    hs, ls = hi_slope / scale, lo_slope / scale
    kind = None
    direction = "neutral"
    if abs(hs) < flat_slope and ls > flat_slope:
        kind, direction = "ascending_triangle", "bullish"
    elif hs < -flat_slope and abs(ls) < flat_slope:
        kind, direction = "descending_triangle", "bearish"
    elif hs < -flat_slope and ls > flat_slope:
        kind, direction = "symmetrical_triangle", "neutral"
    if kind is None:
        return None
    return {
        "name": kind, "type": "classical", "direction": direction,
        "upper_slope_per_bar": round(hi_slope, 6),
        "lower_slope_per_bar": round(lo_slope, 6),
        "apex_converging": bool(hi_slope < lo_slope),
        "completion_pct": None,  # breakout confirms; direction of break decides
        "base_rate": None,
    }


def _progress(last: float, extreme: float, neckline: float) -> float:
    """How far price has travelled from the pattern extreme toward the neckline."""
    span = abs(extreme - neckline)
    if span == 0:
        return 0.0
    return max(0.0, min(100.0, abs(extreme - last) / span * 100.0))


def detect_classical(series: OHLCV, **kwargs) -> List[dict]:
    """Run all classical detectors and return the ones that fire."""
    out = []
    for fn in (detect_double_top, detect_double_bottom, detect_triangle):
        res = fn(series, **_filter_kwargs(fn, kwargs))
        if res:
            out.append(res)
    for inv in (False, True):
        res = detect_head_and_shoulders(series, inverse=inv, **_filter_kwargs(detect_head_and_shoulders, kwargs))
        if res:
            out.append(res)
    return out


def _filter_kwargs(fn, kwargs) -> dict:
    import inspect

    allowed = set(inspect.signature(fn).parameters)
    return {k: v for k, v in kwargs.items() if k in allowed}
