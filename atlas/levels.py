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
    volume: float = 0.0
    strength: float = 0.0

    def to_dict(self) -> dict:
        return {
            "price": round(self.price, 4),
            "kind": self.kind,
            "touches": self.touches,
            "last_touch_index": self.last_touch_index,
            "volume": round(self.volume, 0),
            "strength": round(self.strength, 2),
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
    resistances = _cluster(series.high, series.volume, swings["highs"], tolerance_pct, "resistance")
    supports = _cluster(series.low, series.volume, swings["lows"], tolerance_pct, "support")
    _score_strength(resistances + supports)
    last = len(series) - 1
    return {
        "support": [l.to_dict() for l in sorted(supports, key=lambda x: -x.price)],
        "resistance": [l.to_dict() for l in sorted(resistances, key=lambda x: x.price)],
        "recent_high": max(series.high) if len(series) else None,
        "recent_low": min(series.low) if len(series) else None,
        "last_close": series.close[last] if len(series) else None,
    }


def _cluster(prices, volumes, indices: List[int], tolerance_pct: float, kind: str) -> List[Level]:
    levels: List[Level] = []
    for idx in indices:
        p = prices[idx]
        merged = False
        for lv in levels:
            if lv.price and abs(p - lv.price) / lv.price * 100.0 <= tolerance_pct:
                # Weighted-average the level price and bump the touch count + volume.
                total = lv.touches + 1
                lv.price = (lv.price * lv.touches + p) / total
                lv.touches = total
                lv.volume += volumes[idx]
                lv.last_touch_index = max(lv.last_touch_index, idx)
                merged = True
                break
        if not merged:
            levels.append(Level(price=p, kind=kind, touches=1, last_touch_index=idx, volume=volumes[idx]))
    return levels


def _score_strength(levels: List[Level]) -> None:
    """Strength = touch-count boosted by the level's share of touch-volume (B6)."""
    max_vol = max((l.volume for l in levels), default=0.0)
    for l in levels:
        vol_share = (l.volume / max_vol) if max_vol > 0 else 0.0
        l.strength = l.touches * (1.0 + vol_share)


def classify_by_price(series: OHLCV, **kwargs) -> dict:
    """Relabel detected pivots by position relative to the last close.

    Detection labels levels by swing type (lows vs highs), which reads backwards
    in a strong trend (old swing highs sit *below* price, recent swing lows sit
    *above* it). Here every pivot below the last close is support and every pivot
    above it is resistance — the intuitive view — nearest first, each with its
    distance from price.
    """
    lv = detect_levels(series, **kwargs)
    close = lv["last_close"]
    if close is None:
        return {"support": [], "resistance": [], "last_close": None}

    def _tag(entry):
        d = (entry["price"] - close) / close * 100.0
        return {"price": entry["price"], "touches": entry["touches"], "distance_pct": round(d, 2)}

    pivots = lv["support"] + lv["resistance"]
    support = sorted((_tag(p) for p in pivots if p["price"] <= close), key=lambda x: -x["price"])
    resistance = sorted((_tag(p) for p in pivots if p["price"] > close), key=lambda x: x["price"])
    return {"support": support, "resistance": resistance, "last_close": close}


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


# --------------------------------------------------------------------------- #
# B1/B2. Trendlines & channels
# --------------------------------------------------------------------------- #
def _fit_line(points):
    """Least-squares (slope, intercept) over [(x, y), ...]; None if degenerate."""
    n = len(points)
    if n < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / den
    return slope, my - slope * mx


def detect_trendlines(series: OHLCV, left: int = 2, right: int = 2,
                      tolerance_pct: float = 1.5, max_points: int = 5) -> dict:
    """Fit dynamic support/resistance trendlines through recent swing pivots.

    Each trendline reports slope (per bar), its value projected to the last bar,
    and how many pivots lie on it (touches).
    """
    sw = swing_points(series, left, right)
    last = len(series) - 1

    def build(indices, price_arr, kind):
        pts = [(i, price_arr[i]) for i in indices][-max_points:]
        fit = _fit_line(pts)
        if not fit:
            return None
        slope, intercept = fit
        touches = sum(
            1 for i in indices
            if price_arr[i] and abs(price_arr[i] - (slope * i + intercept)) / price_arr[i] * 100.0 <= tolerance_pct
        )
        return {
            "slope": round(slope, 6),
            "intercept": round(intercept, 4),
            "current_value": round(slope * last + intercept, 4),
            "touches": touches,
            "points": [[i, round(price_arr[i], 4)] for i, _ in pts],
            "direction": "rising" if slope > 0 else "falling" if slope < 0 else "flat",
        }

    return {
        "support": build(sw["lows"], series.low, "support"),
        "resistance": build(sw["highs"], series.high, "resistance"),
    }


def detect_channels(series: OHLCV, **kwargs) -> Optional[dict]:
    """A channel when the support and resistance trendlines are roughly parallel."""
    tl = detect_trendlines(series, **kwargs)
    sup, res = tl["support"], tl["resistance"]
    if not sup or not res:
        return None
    scale = abs(sup["slope"]) + abs(res["slope"]) or 1e-9
    parallel = abs(sup["slope"] - res["slope"]) <= 0.35 * scale
    return {
        "upper": res["current_value"],
        "lower": sup["current_value"],
        "width": round(res["current_value"] - sup["current_value"], 4),
        "support_slope": sup["slope"],
        "resistance_slope": res["slope"],
        "parallel": parallel,
        "type": ("ascending" if sup["slope"] > 0 and res["slope"] > 0 else
                 "descending" if sup["slope"] < 0 and res["slope"] < 0 else "horizontal/other"),
    }


# --------------------------------------------------------------------------- #
# B3. Pivot points (classic / Camarilla / Woodie)
# --------------------------------------------------------------------------- #
def pivot_points(series: OHLCV) -> Optional[dict]:
    """Classic, Camarilla, and Woodie pivots from the last completed bar."""
    if len(series) == 0:
        return None
    i = len(series) - 1
    H, L, C = series.high[i], series.low[i], series.close[i]
    rng = H - L

    p = (H + L + C) / 3.0
    classic = {
        "P": p, "R1": 2 * p - L, "S1": 2 * p - H, "R2": p + rng, "S2": p - rng,
        "R3": H + 2 * (p - L), "S3": L - 2 * (H - p),
    }
    cam = {
        "R1": C + rng * 1.1 / 12, "S1": C - rng * 1.1 / 12,
        "R2": C + rng * 1.1 / 6, "S2": C - rng * 1.1 / 6,
        "R3": C + rng * 1.1 / 4, "S3": C - rng * 1.1 / 4,
        "R4": C + rng * 1.1 / 2, "S4": C - rng * 1.1 / 2,
    }
    wp = (H + L + 2 * C) / 4.0
    woodie = {"P": wp, "R1": 2 * wp - L, "S1": 2 * wp - H, "R2": wp + rng, "S2": wp - rng}

    def _round(d):
        return {k: round(v, 4) for k, v in d.items()}

    return {
        "based_on": {"high": H, "low": L, "close": C,
                     "date": series.ts[i].isoformat() if series.ts else None},
        "classic": _round(classic),
        "camarilla": _round(cam),
        "woodie": _round(woodie),
    }


# --------------------------------------------------------------------------- #
# B4. Gap detection
# --------------------------------------------------------------------------- #
def detect_gaps(series: OHLCV, min_pct: float = 0.5) -> List[dict]:
    """Detect up/down gaps and whether price has since filled them."""
    n = len(series)
    gaps: List[dict] = []
    for i in range(1, n):
        prev_high, prev_low = series.high[i - 1], series.low[i - 1]
        if series.low[i] > prev_high:  # gap up
            size = series.low[i] - prev_high
            pct = size / prev_high * 100.0 if prev_high else 0.0
            if pct >= min_pct:
                filled = any(series.low[j] <= prev_high for j in range(i + 1, n))
                gaps.append(_gap(series, i, "up", prev_high, series.low[i], pct, filled))
        elif series.high[i] < prev_low:  # gap down
            size = prev_low - series.high[i]
            pct = size / prev_low * 100.0 if prev_low else 0.0
            if pct >= min_pct:
                filled = any(series.high[j] >= prev_low for j in range(i + 1, n))
                gaps.append(_gap(series, i, "down", series.high[i], prev_low, pct, filled))
    return gaps


def _gap(series, i, direction, lo, hi, pct, filled) -> dict:
    return {
        "index": i,
        "date": series.ts[i].isoformat() if series.ts else None,
        "type": direction,
        "from": round(lo, 4),
        "to": round(hi, 4),
        "size_pct": round(pct, 2),
        "filled": filled,
    }


# --------------------------------------------------------------------------- #
# B5. Volume-profile levels
# --------------------------------------------------------------------------- #
def volume_profile_levels(series: OHLCV, bins: int = 20, value_area_pct: float = 0.70):
    """POC / value-area / HVN-LVN levels (delegates to the volume-profile indicator)."""
    from .indicators import volume_profile

    return volume_profile(series, bins=bins, value_area_pct=value_area_pct)
