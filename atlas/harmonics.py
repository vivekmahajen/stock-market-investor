"""Harmonic-pattern detection (Section 5).

Detects Gartley, Bat, Butterfly, Crab, Cypher, and Shark patterns from the
series' swing pivots by matching Fibonacci leg ratios. Each detection reports
its geometry, a Potential Reversal Zone (PRZ), a measured target, and the
invalidation level — never a claim that it "will" reverse.

Only patterns whose ratios genuinely fit (within tolerance) are returned; a
loose fit is reported with a lower ``confidence``, and nothing is invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .levels import swing_points
from .types import OHLCV

Ratio = Tuple[float, float]  # (low, high) band


@dataclass
class Pivot:
    index: int
    price: float
    kind: str  # "H" | "L"


def alternating_pivots(series: OHLCV, left: int = 2, right: int = 2) -> List[Pivot]:
    """Merge swing highs/lows into a strictly alternating H/L sequence.

    When two consecutive pivots share a type, the more extreme one is kept, so
    the result alternates high-low-high-low as harmonic geometry requires.
    """
    sw = swing_points(series, left, right)
    raw: List[Pivot] = [Pivot(i, series.high[i], "H") for i in sw["highs"]]
    raw += [Pivot(i, series.low[i], "L") for i in sw["lows"]]
    raw.sort(key=lambda p: p.index)

    out: List[Pivot] = []
    for p in raw:
        if out and out[-1].kind == p.kind:
            # Keep the more extreme pivot of the same type.
            if (p.kind == "H" and p.price > out[-1].price) or (p.kind == "L" and p.price < out[-1].price):
                out[-1] = p
        else:
            out.append(p)
    return out


# Point-ratio targets (exact, matched with tolerance) and range bands.
# Keys: ab_xa, ad_xa are point ratios; bc_ab, cd_bc are ranges.
_TEMPLATES: Dict[str, dict] = {
    "gartley":   {"ab_xa": 0.618, "bc_ab": (0.382, 0.886), "cd_bc": (1.13, 1.618), "ad_xa": 0.786},
    "bat":       {"ab_xa": (0.382, 0.5), "bc_ab": (0.382, 0.886), "cd_bc": (1.618, 2.618), "ad_xa": 0.886},
    "butterfly": {"ab_xa": 0.786, "bc_ab": (0.382, 0.886), "cd_bc": (1.618, 2.618), "ad_xa": (1.272, 1.618)},
    "crab":      {"ab_xa": (0.382, 0.618), "bc_ab": (0.382, 0.886), "cd_bc": (2.618, 3.618), "ad_xa": 1.618},
    "shark":     {"ab_xa": (1.13, 1.618), "bc_ab": (1.618, 2.24), "cd_bc": None, "ad_xa": (0.886, 1.13)},
}

_TOL = 0.05  # absolute tolerance on point ratios


def _match_point(value: float, target: float, tol: float = _TOL) -> Optional[float]:
    """Closeness in [0,1] if within tolerance, else None."""
    if abs(value - target) <= tol:
        return 1.0 - abs(value - target) / tol
    return None


def _match_range(value: float, band: Ratio) -> Optional[float]:
    lo, hi = band
    if lo <= value <= hi:
        mid = (lo + hi) / 2
        half = (hi - lo) / 2 or 1e-9
        return 1.0 - abs(value - mid) / half
    return None


def _score_leg(value: float, spec) -> Optional[float]:
    if spec is None:
        return 1.0
    if isinstance(spec, tuple):
        return _match_range(value, spec)
    return _match_point(value, spec)


def detect_harmonics(series: OHLCV, left: int = 2, right: int = 2) -> List[dict]:
    """Return harmonic patterns whose most-recent 5 pivots (X-A-B-C-D) fit a
    template. D is the latest confirmed pivot (the completion point)."""
    pivots = alternating_pivots(series, left, right)
    if len(pivots) < 5:
        return []
    X, A, B, C, D = pivots[-5:]

    xa = abs(A.price - X.price)
    ab = abs(B.price - A.price)
    bc = abs(C.price - B.price)
    cd = abs(D.price - C.price)
    ad = abs(D.price - A.price)
    if min(xa, ab, bc) <= 0:
        return []

    ab_xa = ab / xa
    bc_ab = bc / ab
    cd_bc = cd / bc if bc else 0.0
    ad_xa = ad / xa

    direction = "bullish" if D.kind == "L" else "bearish"
    results: List[dict] = []
    for name, tpl in _TEMPLATES.items():
        parts = [
            _score_leg(ab_xa, tpl["ab_xa"]),
            _score_leg(bc_ab, tpl["bc_ab"]),
            _score_leg(cd_bc, tpl["cd_bc"]),
            _score_leg(ad_xa, tpl["ad_xa"]),
        ]
        if any(p is None for p in parts):
            continue
        confidence = sum(parts) / len(parts)

        # PRZ around D; measured targets are retracements of the AD leg from D.
        prz_width = 0.02 * xa
        t1 = D.price + (0.382 * ad if direction == "bullish" else -0.382 * ad)
        t2 = D.price + (0.618 * ad if direction == "bullish" else -0.618 * ad)
        invalidation = X.price  # beyond X negates the pattern
        results.append({
            "name": name,
            "type": "harmonic",
            "direction": direction,
            "confidence": round(confidence, 3),
            "points": {
                "X": [X.index, round(X.price, 4)],
                "A": [A.index, round(A.price, 4)],
                "B": [B.index, round(B.price, 4)],
                "C": [C.index, round(C.price, 4)],
                "D": [D.index, round(D.price, 4)],
            },
            "ratios": {
                "AB/XA": round(ab_xa, 3), "BC/AB": round(bc_ab, 3),
                "CD/BC": round(cd_bc, 3), "AD/XA": round(ad_xa, 3),
            },
            "prz": [round(D.price - prz_width, 4), round(D.price + prz_width, 4)],
            "targets": [round(t1, 4), round(t2, 4)],
            "invalidation": round(invalidation, 4),
            "completion_pct": 100.0,  # D has formed as a pivot
            "base_rate": None,        # no historical study wired in — not fabricated
        })
    # Cypher: uses XC (not BC) extension and D as a 0.786 retracement of XC.
    xc = abs(C.price - X.price)
    cypher = _score_cypher(ab_xa, xc / xa if xa else 0.0, abs(D.price - C.price) / xc if xc else 0.0)
    if cypher is not None:
        prz_width = 0.02 * xa
        t1 = D.price + (0.382 * ad if direction == "bullish" else -0.382 * ad)
        t2 = D.price + (0.618 * ad if direction == "bullish" else -0.618 * ad)
        results.append({
            "name": "cypher", "type": "harmonic", "direction": direction,
            "confidence": round(cypher, 3),
            "points": {k: [getattr(pt, "index"), round(pt.price, 4)]
                       for k, pt in (("X", X), ("A", A), ("B", B), ("C", C), ("D", D))},
            "ratios": {"AB/XA": round(ab_xa, 3), "XC/XA": round(xc / xa, 3) if xa else None,
                       "DC/XC": round(abs(D.price - C.price) / xc, 3) if xc else None},
            "prz": [round(D.price - prz_width, 4), round(D.price + prz_width, 4)],
            "targets": [round(t1, 4), round(t2, 4)],
            "invalidation": round(X.price, 4),
            "completion_pct": 100.0, "base_rate": None,
        })

    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results


def _score_cypher(ab_xa: float, xc_xa: float, dc_xc: float) -> Optional[float]:
    """Cypher fit: AB 0.382-0.618 XA, C 1.13-1.414 XA extension, D 0.786 of XC."""
    p1 = _match_range(ab_xa, (0.382, 0.618))
    p2 = _match_range(xc_xa, (1.13, 1.414))
    p3 = _match_point(dc_xc, 0.786, tol=0.06)
    if p1 is None or p2 is None or p3 is None:
        return None
    return (p1 + p2 + p3) / 3.0
