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
from .events import build_event_risk, event_risk_note
from .fundamentals import fundamental_subscore, sentiment_subscore
from .chart_patterns import detect_classical
from .fibonacci import auto_fibonacci
from .harmonics import detect_harmonics
from .levels import classify_by_price, nearest_levels
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
    with_fundamentals: bool = False,
    with_sentiment: bool = False,
    with_events: bool = False,
) -> dict:
    """Full workup for ``analyze <symbol>`` producing the Section 15 envelope.

    ``with_fundamentals`` / ``with_sentiment`` opt into extra data-feed calls
    (each an API request) that fill the fundamental and sentiment sub-scores.
    Left off, those sub-scores are ``None`` — the honest default, not a
    fabricated value.
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
    notes = []
    if benchmark:
        bench = registry.get_ohlcv(benchmark, timeframe, lookback)
        if "_series" in bench:
            rs = scoring.relative_strength_subscore(series, bench["_series"])
            if rs is None:
                notes.append(
                    f"relative_strength is null: not enough overlapping history between "
                    f"{symbol} and benchmark {benchmark} (need >63 common bars)."
                )
        else:
            notes.append(
                f"relative_strength is null: benchmark '{benchmark}' could not be loaded "
                f"({bench.get('error', 'unknown error')})."
            )
    else:
        notes.append(
            "relative_strength is null: no benchmark given. Pass benchmark=<symbol> "
            "(and provide that symbol's data) to compute relative strength."
        )

    fund = None
    fundamentals_detail = None
    if with_fundamentals:
        fr = registry.get_fundamentals(symbol)
        if "error" in fr:
            notes.append(f"fundamental is null: {fr['error']}.")
        else:
            fundamentals_detail = fundamental_subscore(fr["overview"])
            if fundamentals_detail is None:
                notes.append("fundamental is null: too few fundamental factors available for a score.")
            else:
                fund = fundamentals_detail["score"]
    else:
        notes.append("fundamental is null: pass with_fundamentals=True (a fundamentals feed) to compute it.")

    sent = None
    sentiment_detail = None
    if with_sentiment:
        nr = registry.get_news_sentiment(symbol)
        if "error" in nr:
            notes.append(f"sentiment is null: {nr['error']}.")
        else:
            sentiment_detail = sentiment_subscore(nr["news"], symbol)
            if sentiment_detail is None:
                notes.append("sentiment is null: news feed returned no usable articles.")
            else:
                sent = sentiment_detail["score"]
    else:
        notes.append("sentiment is null: pass with_sentiment=True (a news feed) to compute it.")

    events = []
    if with_events:
        er = registry.get_earnings_calendar(symbol)
        if "error" in er:
            notes.append(f"events unchecked: {er['error']}.")
        else:
            asof_date = series.asof.date() if series.asof else None
            if asof_date:
                events = build_event_risk(er["earnings"], asof_date)
                warn = event_risk_note(events)
                if warn:
                    notes.append(warn)
                elif not events:
                    notes.append("No earnings within the event-risk window.")
    else:
        notes.append("events unchecked: pass with_events=True to check the earnings calendar before acting.")

    subscores = {
        "technical": tech,
        "fundamental": fund,
        "sentiment": sent,
        "relative_strength": rs,
        "risk": _risk_subscore(series),
    }
    score = scoring.atlas_score(subscores)

    levels = classify_by_price(series)
    near = nearest_levels(series)
    patterns = {
        "candlestick": latest_patterns(series, lookback=5),
        "classical": detect_classical(series),
        "harmonic": detect_harmonics(series),
    }
    fib = auto_fibonacci(series)

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
        "fibonacci": fib,
        "patterns": patterns,
        "fundamentals_detail": fundamentals_detail,
        "sentiment_detail": sentiment_detail,
        "events": events,
        "notes": notes,
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
    thesis: Optional[str] = None,
    confidence: Optional[float] = None,
    confidence_drivers: Optional[List[str]] = None,
    biggest_risk: Optional[str] = None,
    invalidation: Optional[str] = None,
    catalyst_or_expiry: Optional[str] = None,
    regime: Optional[str] = None,
    min_r: float = 1.5,
) -> dict:
    """Assemble a fully risk-defined signal (Section 6). Rejects sub-threshold R.

    The narrative fields (thesis, confidence, biggest_risk, invalidation,
    catalyst) are optional here and auto-filled by :func:`propose_signal`; when
    omitted, ``invalidation`` is derived from the stop so a manual plan still
    carries its "what would make me wrong".
    """
    size = size_position(account_equity, entry, stop, direction, risk_pct)
    r1 = r_multiple(entry, stop, targets[0], direction) if targets else None
    warnings = list(size.warnings)
    if r1 is not None and r1 < min_r:
        warnings.append(f"R:R to first target is {r1:.2f} (< {min_r}) — setup rejected by default threshold.")
    if events:
        note = event_risk_note(events)
        if note:
            warnings.append(note)
    if invalidation is None:
        side = "below" if direction == "long" else "above"
        invalidation = f"A decisive close {side} the stop at {stop} negates the {direction}."
    return {
        "symbol": symbol,
        "direction": direction,
        "regime": regime,
        "thesis": thesis,
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "r_multiple": round(r1, 2) if r1 is not None else None,
        "position_size": size.to_dict(),
        "confidence": confidence,
        "confidence_drivers": confidence_drivers or [],
        "confidence_basis": "model-derived (technical confluence, not yet a calibrated base rate)",
        "biggest_risk": biggest_risk,
        "what_would_make_me_wrong": invalidation,
        "horizon": horizon,
        "catalyst_or_expiry": catalyst_or_expiry,
        "events": events or [],
        "warnings": warnings,
        "disclaimer": "Educational analysis, not financial advice. You decide; consider a licensed advisor.",
    }


# --------------------------------------------------------------------------- #
# Auto-derived signal proposal (Section 6)
# --------------------------------------------------------------------------- #
def _last_atr(series: OHLCV, period: int = 14) -> Optional[float]:
    for v in reversed(ind.atr(series, period)):
        if v is not None:
            return v
    return None


def _pattern_tally(series: OHLCV):
    bull = bear = 0
    groups = [latest_patterns(series, 5), detect_classical(series), detect_harmonics(series)]
    for g in groups:
        for p in g:
            if p.get("direction") == "bullish":
                bull += 1
            elif p.get("direction") == "bearish":
                bear += 1
    return bull, bear


def _decide_direction(regime, tech, conf_score, bull, bear) -> str:
    if tech is None or conf_score is None:
        return "flat"
    if regime == "trending_up" and tech >= 55 and conf_score >= 55 and bull >= bear:
        return "long"
    if regime == "trending_down" and tech <= 45 and conf_score <= 45 and bear >= bull:
        return "short"
    return "flat"  # range / high-vol / conflicting -> no clean directional setup


def _derive_stop_targets(direction, entry, levels, atrv, fib):
    atrv = atrv or entry * 0.02
    if direction == "long":
        supports = [s["price"] for s in levels["support"] if s["price"] < entry]
        stop = (supports[0] - 0.25 * atrv) if supports else (entry - 1.5 * atrv)
        if stop >= entry:
            stop = entry - 1.5 * atrv
        risk = entry - stop
        tgts = [r["price"] for r in levels["resistance"] if r["price"] > entry]
        if fib and fib.get("extensions"):
            tgts += [float(v) for v in fib["extensions"].values() if float(v) > entry]
        while len(tgts) < 2:
            tgts.append(entry + (len(tgts) + 2) * risk)
        tgts = sorted({round(t, 4) for t in tgts})[:3]
    else:
        resist = [r["price"] for r in levels["resistance"] if r["price"] > entry]
        stop = (resist[0] + 0.25 * atrv) if resist else (entry + 1.5 * atrv)
        if stop <= entry:
            stop = entry + 1.5 * atrv
        risk = stop - entry
        tgts = [s["price"] for s in levels["support"] if s["price"] < entry]
        if fib and fib.get("extensions"):
            tgts += [float(v) for v in fib["extensions"].values() if float(v) < entry]
        while len(tgts) < 2:
            tgts.append(entry - (len(tgts) + 2) * risk)
        tgts = sorted({round(t, 4) for t in tgts}, reverse=True)[:3]
    return round(stop, 4), tgts


def _signal_confidence(conf_score, tech, bull, bear, direction, events):
    total = bull + bear
    align = 50.0
    if total > 0:
        net = (bull - bear) / total
        align = 50.0 + (net if direction == "long" else -net) * 50.0
    base = 0.4 * conf_score + 0.4 * tech + 0.2 * align
    drivers = [f"confluence {conf_score:.0f}", f"technical {tech:.0f}", f"pattern alignment {align:.0f}"]
    if events and events[0].get("risk") == "high":
        base -= 15
        drivers.append(f"-15 imminent earnings ({events[0]['days_away']}d)")
    return max(0, min(100, round(base))), drivers


def propose_signal(
    symbol: str,
    registry: Optional[ToolRegistry] = None,
    series: Optional[OHLCV] = None,
    account_equity: float = 100_000.0,
    risk_pct: float = 0.01,
    timeframe: str = "1d",
    lookback: int = 300,
    horizon: str = "swing (days-weeks)",
    min_r: float = 1.5,
    with_events: bool = False,
) -> dict:
    """Auto-derive a fully-specified signal from the analysis (Section 6).

    Direction comes from regime + technical + confluence + pattern alignment;
    stop and targets from structure/Fibonacci; confidence from the blend. Returns
    a ``flat`` result (with a reason) when there is no clean setup — silence is a
    valid answer per the spec.
    """
    registry = registry or ToolRegistry()
    if series is None:
        fetched = registry.get_ohlcv(symbol, timeframe, lookback)
        if "error" in fetched:
            return {"symbol": symbol, "error": fetched["error"]}
        series = fetched["_series"]

    regime = classify_regime(series)
    conf = confluence_score(series)
    conf_score = conf.get("score")
    tech = scoring.technical_subscore(series)
    bull, bear = _pattern_tally(series)
    direction = _decide_direction(regime, tech, conf_score, bull, bear)

    events = []
    if with_events and series.asof:
        er = registry.get_earnings_calendar(symbol)
        if "earnings" in er:
            events = build_event_risk(er["earnings"], series.asof.date())

    if direction == "flat":
        return {
            "symbol": symbol,
            "direction": "flat",
            "reason": f"No clean setup: regime={regime}, technical={tech}, confluence={conf_score}, "
                      f"patterns bull/bear={bull}/{bear}. Silence is a valid output.",
            "regime": regime,
            "events": events,
            "disclaimer": "Educational analysis, not financial advice.",
        }

    entry = round(series.close[-1], 4)
    atrv = _last_atr(series)
    levels = classify_by_price(series)
    fib = auto_fibonacci(series)
    stop, targets = _derive_stop_targets(direction, entry, levels, atrv, fib)
    confidence, drivers = _signal_confidence(conf_score, tech, bull, bear, direction, events)

    side = "Long" if direction == "long" else "Short"
    pat = "bullish" if bull > bear else "bearish" if bear > bull else "mixed"
    thesis = (f"{side} {symbol}: {regime} regime, technical {tech:.0f}/100 and confluence "
              f"{conf_score:.0f}/100, {pat} pattern read; entry near {entry} against structure.")

    if events and events[0].get("risk") in ("high", "medium"):
        biggest_risk = f"earnings on {events[0]['date']} ({events[0]['days_away']}d) can gap through the stop"
    elif tech < 65:
        biggest_risk = "momentum is only partially confirmed; a stall would invalidate the thesis"
    else:
        biggest_risk = f"a regime flip out of the current {regime}"

    side_word = "below" if direction == "long" else "above"
    invalidation = (f"A decisive daily close {side_word} the stop at {stop} negates the {direction}; "
                    f"an earlier warning is price losing the {regime} structure.")
    catalyst = (f"invalid after earnings on {events[0]['date']}" if events
                else f"thesis expires at the end of the {horizon} window")

    return build_signal(
        symbol, entry, stop, targets, direction, account_equity, risk_pct, horizon, events,
        thesis=thesis, confidence=confidence, confidence_drivers=drivers,
        biggest_risk=biggest_risk, invalidation=invalidation,
        catalyst_or_expiry=catalyst, regime=regime, min_r=min_r,
    )
