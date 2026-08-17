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


def detect_triple(series: OHLCV, tol_pct: float = 2.5, left: int = 2, right: int = 2,
                  top: bool = True) -> Optional[dict]:
    """Triple top/bottom: three swing highs/lows at a similar level."""
    sw = swing_points(series, left, right)
    idx = sw["highs"] if top else sw["lows"]
    prices_arr = series.high if top else series.low
    if len(idx) < 3:
        return None
    i1, i2, i3 = idx[-3:]
    ps = [prices_arr[i1], prices_arr[i2], prices_arr[i3]]
    if (max(ps) - min(ps)) / max(ps) * 100.0 > tol_pct:
        return None
    level = sum(ps) / 3
    opp = sw["lows"] if top else sw["highs"]
    opp_arr = series.low if top else series.high
    between = [o for o in opp if i1 < o < i3]
    if not between:
        return None
    neckline = (max if top else min)(opp_arr[o] for o in between)
    height = abs(level - neckline)
    last = series.close[-1]
    confirmed = last < neckline if top else last > neckline
    return {
        "name": "triple_top" if top else "triple_bottom", "type": "classical",
        "direction": "bearish" if top else "bullish",
        "level": round(level, 4), "neckline": round(neckline, 4),
        "target": round(neckline - height if top else neckline + height, 4),
        "invalidation": round(level, 4),
        "completion_pct": 100.0 if confirmed else round(_progress(last, level, neckline), 1),
        "confirmed": confirmed, "base_rate": None,
    }


def _swing_slopes(series: OHLCV, k: int, left: int, right: int):
    sw = swing_points(series, left, right)
    highs, lows = sw["highs"][-k:], sw["lows"][-k:]
    if len(highs) < 2 or len(lows) < 2:
        return None
    scale = series.close[-1] or 1.0
    hs = _slope(highs, [series.high[i] for i in highs]) / scale
    ls = _slope(lows, [series.low[i] for i in lows]) / scale
    return hs, ls, highs, lows


def detect_wedge_or_broadening(series: OHLCV, k: int = 4, left: int = 2, right: int = 2,
                               flat: float = 1e-4) -> Optional[dict]:
    """Rising/falling wedge (converging, sloped) or broadening formation (diverging)."""
    res = _swing_slopes(series, k, left, right)
    if not res:
        return None
    hs, ls, highs, lows = res
    # Broadening: highs rising, lows falling (diverging megaphone).
    if hs > flat and ls < -flat:
        name, direction = "broadening_formation", "neutral"
    # Converging wedges: lower line steeper than upper (ls > hs).
    elif ls > hs + flat and hs > flat and ls > flat:
        name, direction = "rising_wedge", "bearish"
    elif ls > hs + flat and hs < -flat and ls < -flat:
        name, direction = "falling_wedge", "bullish"
    else:
        return None
    return {
        "name": name, "type": "classical", "direction": direction,
        "upper_slope": round(hs, 6), "lower_slope": round(ls, 6),
        "completion_pct": None, "base_rate": None,
    }


def detect_rectangle(series: OHLCV, k: int = 4, left: int = 2, right: int = 2,
                     flat: float = 8e-5) -> Optional[dict]:
    """Rectangle: flat swing highs and flat swing lows (horizontal range)."""
    res = _swing_slopes(series, k, left, right)
    if not res:
        return None
    hs, ls, highs, lows = res
    if abs(hs) > flat or abs(ls) > flat:
        return None
    top = sum(series.high[i] for i in highs) / len(highs)
    bottom = sum(series.low[i] for i in lows) / len(lows)
    if top <= bottom:
        return None
    return {
        "name": "rectangle", "type": "classical", "direction": "neutral",
        "resistance": round(top, 4), "support": round(bottom, 4),
        "height": round(top - bottom, 4), "completion_pct": None, "base_rate": None,
    }


def detect_rounding(series: OHLCV, window: int = 40) -> Optional[dict]:
    """Rounding top/bottom via the curvature of a quadratic fit over ``window``."""
    n = len(series)
    if n < window:
        return None
    ys = list(series.close[-window:])
    xs = list(range(window))
    mx = sum(xs) / window
    my = sum(ys) / window
    # Fit y = a x^2 + b x + c via normal equations (small, well-conditioned).
    sxx = sum((x - mx) ** 2 for x in xs)
    x2 = [x * x for x in xs]
    mx2 = sum(x2) / window
    # Solve 2x2 for a,b using centered basis (x, x^2) vs y.
    s_x2y = sum((x2[k] - mx2) * (ys[k] - my) for k in range(window))
    s_xy = sum((xs[k] - mx) * (ys[k] - my) for k in range(window))
    s_x2x2 = sum((x2[k] - mx2) ** 2 for k in range(window))
    s_x2x = sum((x2[k] - mx2) * (xs[k] - mx) for k in range(window))
    det = s_x2x2 * sxx - s_x2x ** 2
    if det == 0:
        return None
    a = (s_x2y * sxx - s_xy * s_x2x) / det
    curvature = a * window * window / (my or 1.0)  # scale-free curvature proxy
    if curvature > 0.08:
        name, direction = "rounding_bottom", "bullish"
    elif curvature < -0.08:
        name, direction = "rounding_top", "bearish"
    else:
        return None
    return {
        "name": name, "type": "classical", "direction": direction,
        "curvature": round(curvature, 4), "window": window,
        "completion_pct": None, "base_rate": None,
    }


def detect_flag_pennant(series: OHLCV, pole: int = 12, cons: int = 7,
                        pole_move: float = 0.08) -> Optional[dict]:
    """Flag / pennant: a strong pole then a small counter-trend consolidation."""
    n = len(series)
    if n < pole + cons + 1:
        return None
    p_start = n - pole - cons
    p_end = n - cons
    if series.close[p_start] <= 0:
        return None
    pole_ret = series.close[p_end - 1] / series.close[p_start] - 1.0
    cons_slice = series.close[p_end:]
    cons_ret = cons_slice[-1] / cons_slice[0] - 1.0 if cons_slice[0] else 0.0
    cons_range = (max(series.high[p_end:]) - min(series.low[p_end:])) / series.close[p_end - 1]
    if cons_range > abs(pole_ret):  # consolidation should be tighter than the pole
        return None
    # Pennant if the consolidation converges (range shrinking), else a flag.
    first_half = max(series.high[p_end:p_end + cons // 2]) - min(series.low[p_end:p_end + cons // 2])
    second_half = max(series.high[p_end + cons // 2:]) - min(series.low[p_end + cons // 2:])
    converging = second_half < first_half
    if pole_ret >= pole_move and cons_ret <= 0:
        name = ("bull_pennant" if converging else "bull_flag")
        return _flag_dict(name, "bullish", pole_ret, cons_ret)
    if pole_ret <= -pole_move and cons_ret >= 0:
        name = ("bear_pennant" if converging else "bear_flag")
        return _flag_dict(name, "bearish", pole_ret, cons_ret)
    return None


def _flag_dict(name, direction, pole_ret, cons_ret):
    return {
        "name": name, "type": "classical", "direction": direction,
        "pole_return_pct": round(pole_ret * 100, 2),
        "consolidation_return_pct": round(cons_ret * 100, 2),
        "completion_pct": None, "base_rate": None,
    }


def detect_cup_and_handle(series: OHLCV, window: int = 45, handle: int = 8,
                          max_depth: float = 0.35) -> Optional[dict]:
    """Cup-and-handle: a rounded cup (decline then recovery to the rim) plus a
    small handle pullback near the rim."""
    n = len(series)
    if n < window:
        return None
    seg = list(series.close[-window:])
    rim_left = seg[0]
    cup = seg[:-handle] if handle < window else seg
    trough = min(cup)
    trough_i = cup.index(trough)
    depth = (rim_left - trough) / rim_left if rim_left else 0.0
    # Cup: trough in the middle, both rims near each other, moderate depth.
    if not (0.05 < depth < max_depth):
        return None
    if not (window * 0.25 < trough_i < window * 0.75):
        return None
    rim_right = max(cup[-5:])
    if abs(rim_right - rim_left) / rim_left > 0.06:
        return None
    handle_slice = seg[-handle:]
    handle_dip = (max(handle_slice) - min(handle_slice)) / rim_right if rim_right else 0.0
    if handle_dip > depth:  # handle must be shallower than the cup
        return None
    return {
        "name": "cup_and_handle", "type": "classical", "direction": "bullish",
        "cup_depth_pct": round(depth * 100, 2), "rim": round(rim_right, 4),
        "target": round(rim_right * (1 + depth), 4),
        "completion_pct": None, "base_rate": None,
    }


def detect_classical(series: OHLCV, **kwargs) -> List[dict]:
    """Run all classical detectors and return the ones that fire."""
    out = []
    for fn in (detect_double_top, detect_double_bottom, detect_triangle,
               detect_wedge_or_broadening, detect_rectangle, detect_rounding,
               detect_flag_pennant, detect_cup_and_handle):
        res = fn(series, **_filter_kwargs(fn, kwargs))
        if res:
            out.append(res)
    for top in (True, False):
        res = detect_triple(series, top=top, **_filter_kwargs(detect_triple, kwargs))
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
