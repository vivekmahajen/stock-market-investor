"""Auto-anchored Fibonacci retracements and extensions (Section 5).

Anchored to the most recent significant swing so the levels are objective, not
hand-drawn. Retracements measure how far a move has pulled back; extensions
project measured-move targets beyond it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .levels import swing_points
from .types import OHLCV

RETRACEMENT_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
EXTENSION_RATIOS = [1.272, 1.414, 1.618, 2.0, 2.618]


def fib_retracements(swing_high: float, swing_low: float, direction: str = "up") -> Dict[str, float]:
    """Retracement levels between a swing low and high.

    ``direction='up'`` treats the impulse as low->high (retracements sit below
    the high); ``'down'`` treats it as high->low.
    """
    rng = swing_high - swing_low
    levels: Dict[str, float] = {}
    for r in RETRACEMENT_RATIOS:
        if direction == "up":
            levels[f"{r:.3f}"] = swing_high - rng * r
        else:
            levels[f"{r:.3f}"] = swing_low + rng * r
    return {k: round(v, 4) for k, v in levels.items()}


def fib_extensions(swing_high: float, swing_low: float, direction: str = "up") -> Dict[str, float]:
    """Extension (projection) levels beyond the impulse."""
    rng = swing_high - swing_low
    levels: Dict[str, float] = {}
    for r in EXTENSION_RATIOS:
        if direction == "up":
            levels[f"{r:.3f}"] = swing_low + rng * r
        else:
            levels[f"{r:.3f}"] = swing_high - rng * r
    return {k: round(v, 4) for k, v in levels.items()}


def auto_fibonacci(series: OHLCV, left: int = 3, right: int = 3) -> Optional[dict]:
    """Anchor Fibonacci to the most recent major swing in the series.

    Picks the latest prominent swing high and swing low, infers the impulse
    direction from their order, and returns retracements + extensions. Returns
    ``None`` if two anchor pivots cannot be found.
    """
    sw = swing_points(series, left, right)
    highs, lows = sw["highs"], sw["lows"]
    if not highs or not lows:
        return None

    last_high_i = highs[-1]
    last_low_i = lows[-1]
    hi = series.high[last_high_i]
    lo = series.low[last_low_i]
    if hi <= lo:
        return None

    # If the swing low is more recent than the swing high, the latest impulse is
    # down (high -> low); otherwise up (low -> high).
    direction = "down" if last_low_i > last_high_i else "up"
    return {
        "anchor_high": round(hi, 4),
        "anchor_low": round(lo, 4),
        "anchor_high_index": last_high_i,
        "anchor_low_index": last_low_i,
        "direction": direction,
        "retracements": fib_retracements(hi, lo, direction),
        "extensions": fib_extensions(hi, lo, direction),
    }
