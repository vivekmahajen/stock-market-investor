"""High-level orchestration: regime, confluence, and the layered analysis
output (Sections 4, 6, 14, 15).

This module assembles tool outputs into the structured JSON envelope from
Section 15. It never invents numbers — every field is either computed from the
series or left ``None``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import indicators as ind
from . import scoring
from .levels import nearest_levels
from .patterns import latest_patterns
from .risk import r_multiple, size_position
from .tools import ToolRegistry
from .types import OHLCV


def classify_regime(series: OHLCV) -> str:
    """Trend/range + volatility regime from EMA slope and ATR%."""
    if len(series) < 60:
        return "unknown"
    close = list(series.close)
    ema50 = ind.ema(close, 50)
    atrp = ind.atr_percent(series, 14)
    last = len(series) - 1
    slope = None
    if ema50[last] is not None and ema50[last - 20] is not None:
        slope = (ema50[last] - ema50[last - 20]) / ema50[last - 20]
    vol = atrp[last]
    high_vol = vol is not None and vol > 3.0
    if slope is None:
        return "range"
    if slope > 0.02:
        return "high_vol" if high_vol else "trending_up"
    if slope < -0.02:
        return "high_vol" if high_vol else "trending_down"
    return "high_vol" if high_vol else "range"


def confluence_score(series: OHLCV) -> dict:
    """0-100 technical-confluence score with a component breakdown (Section 4)."""
    close = list(series.close)
    last = len(series) - 1
    components: Dict[str, float] = {}

    ema20, ema50 = ind.ema(close, 20), ind.ema(close, 50)
    if ema20[last] is not None and ema50[last] is not None:
        components["trend"] = 100.0 if close[last] > ema20[last] > ema50[last] else (
            0.0 if close[last] < ema20[last] < ema50[last] else 50.0
        )
    r = ind.rsi(close, 14)[last]
    if r is not None:
        components["momentum"] = max(0.0, min(100.0, (r - 30) / 0.4))
    hist = ind.macd(close)["hist"][last]
    if hist is not None:
        components["macd"] = 100.0 if hist > 0 else 20.0
    a = ind.adx(series)["adx"][last]
    if a is not None:
        components["trend_strength"] = min(100.0, a * 2.5)

    score = sum(components.values()) / len(components) if components else None
    return {"score": round(score, 1) if score is not None else None, "components": {k: round(v, 1) for k, v in components.items()}}


def analyze(
    symbol: str,
    registry: Optional[ToolRegistry] = None,
    timeframe: str = "1d",
    lookback: int = 300,
    benchmark: Optional[str] = None,
) -> dict:
    """Full workup for ``analyze <symbol>`` producing the Section 15 envelope.

    Returns the structured JSON object (schema in Section 15). Sentiment and
    fundamental sub-scores are ``None`` here because this reference build has no
    news/fundamentals feed wired in — the honest default, not a fabricated value.
    """
    registry = registry or ToolRegistry()
    fetched = registry.get_ohlcv(symbol, timeframe, lookback)
    if "error" in fetched:
        return {"symbol": symbol, "error": fetched["error"]}
    series: OHLCV = fetched["_series"]

    regime = classify_regime(series)
    conf = confluence_score(series)
    tech = scoring.technical_subscore(series)

    rs = None
    if benchmark:
        bench = registry.get_ohlcv(benchmark, timeframe, lookback)
        if "_series" in bench:
            rs = scoring.relative_strength_subscore(series, bench["_series"])

    subscores = {
        "technical": tech,
        "fundamental": None,   # no fundamentals feed in this build
        "sentiment": None,     # no news feed in this build
        "relative_strength": rs,
        "risk": _risk_subscore(series),
    }
    score = scoring.atlas_score(subscores)

    levels = registry.detect_levels(series)
    near = nearest_levels(series)
    patterns = latest_patterns(series, lookback=5)

    prov = [fetched["provenance"]]
    simulated = fetched["provenance"].get("simulated", False)

    return {
        "symbol": symbol,
        "asof": fetched["asof"],
        "timeframe_context": timeframe,
        "regime": regime,
        "confluence": conf,
        "atlas_score": score.to_dict()["atlas_score"],
        "subscores": score.to_dict()["subscores"],
        "score_label": score.label,
        "score_horizon": score.horizon,
        "top_contributors": score.contributors,
        "levels": {
            "support": [s["price"] for s in levels["support"][:5]],
            "resistance": [r["price"] for r in levels["resistance"][:5]],
            "nearest_support": near["support_below"],
            "nearest_resistance": near["resistance_above"],
            "last_close": levels["last_close"],
        },
        "patterns": patterns,
        "events": [],  # no calendar feed in this build; must be checked before a live signal
        "data_provenance": prov,
        "data_is_simulated": simulated,
        "disclaimer": "Educational analysis, not financial advice.",
    }


def _risk_subscore(series: OHLCV) -> Optional[float]:
    """Lower volatility & drawdown -> higher risk/quality sub-score."""
    atrp = ind.atr_percent(series, 14)
    v = None
    for x in reversed(atrp):
        if x is not None:
            v = x
            break
    if v is None:
        return None
    # 1% ATR -> ~90, 5% ATR -> ~10 (clamped).
    return max(0.0, min(100.0, 100.0 - (v - 1.0) * 20.0))


def build_signal(
    symbol: str,
    entry: float,
    stop: float,
    targets: List[float],
    direction: str,
    account_equity: float,
    risk_pct: float = 0.01,
    horizon: str = "swing (days-weeks)",
    events: Optional[List[dict]] = None,
) -> dict:
    """Assemble a fully risk-defined signal (Section 6). Rejects sub-threshold R."""
    size = size_position(account_equity, entry, stop, direction, risk_pct)
    r1 = r_multiple(entry, stop, targets[0], direction) if targets else None
    warnings = list(size.warnings)
    if r1 is not None and r1 < 1.5:
        warnings.append(f"R:R to first target is {r1:.2f} (< 1.5) — setup rejected by default threshold.")
    if events:
        warnings.append("Event risk present before/within horizon — see events; consider deferring.")
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "r_multiple": round(r1, 2) if r1 is not None else None,
        "position_size": size.to_dict(),
        "horizon": horizon,
        "events": events or [],
        "warnings": warnings,
        "disclaimer": "Educational analysis, not financial advice. You decide; consider a licensed advisor.",
    }
