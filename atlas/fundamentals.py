"""Fundamental and sentiment sub-scores (Sections 10, 12).

Pure, testable scoring functions that turn an Alpha Vantage ``OVERVIEW`` object
and a ``NEWS_SENTIMENT`` feed into transparent 0-100 sub-scores with
attribution. Every factor is derived from the supplied data; nothing is
invented. When too few inputs are present, the score is ``None`` (never a
fabricated number).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Alpha Vantage encodes missing values as these strings.
_MISSING = {"", "none", "-", "n/a", "nan"}


def _avf(overview: Dict, key: str) -> Optional[float]:
    """Parse an Alpha Vantage numeric string field, or None if absent."""
    v = overview.get(key)
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in _MISSING:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def _piecewise(x: float, points: List[Tuple[float, float]]) -> float:
    """Linear interpolation over (input, score) breakpoints (ascending inputs)."""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + t * (y1 - y0)
    return 50.0


# Factor scorers (fraction inputs, e.g. 0.25 == 25%).
def _profit_margin_score(pm: float) -> float:
    return _clamp(_piecewise(pm, [(-0.10, 5), (0.0, 30), (0.10, 65), (0.20, 85), (0.30, 95)]))


def _roe_score(roe: float) -> float:
    return _clamp(_piecewise(roe, [(-0.10, 10), (0.0, 35), (0.10, 60), (0.20, 82), (0.35, 95)]))


def _growth_score(g: float) -> float:
    return _clamp(_piecewise(g, [(-0.20, 8), (0.0, 40), (0.10, 68), (0.25, 88), (0.50, 96)]))


def _pe_score(pe: float) -> float:
    if pe < 0:
        return 20.0  # negative earnings
    return _clamp(_piecewise(pe, [(5, 82), (15, 78), (25, 62), (40, 45), (70, 25), (120, 12)]))


def _peg_score(peg: float) -> float:
    if peg <= 0:
        return 50.0
    return _clamp(_piecewise(peg, [(0.5, 90), (1.0, 78), (1.5, 62), (2.0, 48), (3.0, 30), (5.0, 15)]))


def fundamental_subscore(overview: Dict) -> Optional[dict]:
    """Blend available fundamental factors into a 0-100 sub-score with attribution.

    Factors (each scored 0-100 then averaged over those present): profit margin,
    ROE, revenue growth, earnings growth, P/E, and PEG. Returns ``None`` if fewer
    than two factors are available.
    """
    factors: Dict[str, float] = {}

    pm = _avf(overview, "ProfitMargin")
    if pm is not None:
        factors["profit_margin"] = _profit_margin_score(pm)
    roe = _avf(overview, "ReturnOnEquityTTM")
    if roe is not None:
        factors["roe"] = _roe_score(roe)
    rev_g = _avf(overview, "QuarterlyRevenueGrowthYOY")
    if rev_g is not None:
        factors["revenue_growth"] = _growth_score(rev_g)
    earn_g = _avf(overview, "QuarterlyEarningsGrowthYOY")
    if earn_g is not None:
        factors["earnings_growth"] = _growth_score(earn_g)
    pe = _avf(overview, "PERatio")
    if pe is not None:
        factors["pe"] = _pe_score(pe)
    peg = _avf(overview, "PEGRatio")
    if peg is not None:
        factors["peg"] = _peg_score(peg)

    if len(factors) < 2:
        return None

    score = sum(factors.values()) / len(factors)
    ranked = sorted(factors.items(), key=lambda kv: abs(kv[1] - 50.0), reverse=True)
    contributors = [
        f"{'+' if v >= 50 else '-'} {k.replace('_', ' ')} ({v:.0f})" for k, v in ranked[:4]
    ]
    return {
        "score": round(score, 1),
        "factors": {k: round(v, 1) for k, v in factors.items()},
        "contributors": contributors,
        "name": overview.get("Name"),
        "sector": overview.get("Sector"),
    }


# Alpha Vantage overall_sentiment_score bands span roughly [-0.35, +0.35].
def sentiment_subscore(news: Dict, symbol: str) -> Optional[dict]:
    """Aggregate an Alpha Vantage NEWS_SENTIMENT feed into a 0-100 sub-score.

    Prefers each article's ticker-specific sentiment (weighted by relevance) for
    ``symbol``, falling back to the article's overall sentiment. Returns ``None``
    if the feed has no usable articles.
    """
    feed = news.get("feed") or []
    sym = symbol.upper()
    weighted_sum = 0.0
    weight_total = 0.0
    used = 0
    label_counts: Dict[str, int] = {}
    for article in feed:
        score = None
        weight = 1.0
        for ts in article.get("ticker_sentiment", []) or []:
            if str(ts.get("ticker", "")).upper() == sym:
                try:
                    score = float(ts.get("ticker_sentiment_score"))
                    weight = float(ts.get("relevance_score") or 1.0)
                except (TypeError, ValueError):
                    score = None
                break
        if score is None:
            try:
                score = float(article.get("overall_sentiment_score"))
            except (TypeError, ValueError):
                continue
        weighted_sum += score * weight
        weight_total += weight
        used += 1
        label = str(article.get("overall_sentiment_label", "")).strip()
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1

    if used == 0 or weight_total == 0:
        return None

    avg = weighted_sum / weight_total
    # Map [-0.35, +0.35] onto [0, 100], clamped.
    score = _clamp(50.0 + avg * (50.0 / 0.35))
    return {
        "score": round(score, 1),
        "avg_sentiment": round(avg, 4),
        "articles": used,
        "label_mix": label_counts,
    }
