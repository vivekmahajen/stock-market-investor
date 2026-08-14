"""Screening & scanning (Section 7 of the ATLAS spec).

Translates a structured filter spec into per-symbol metrics, applies the
filters transparently, ranks by a composite score, and flags liquidity /
tradability problems on every result. The natural-language -> filter-spec
translation is the LLM's job; this module is the deterministic engine it drives,
so the criteria are always auditable ("you asked for X; I scanned for these
exact rules").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import indicators as ind
from .data import DataProvider, SyntheticProvider
from .types import OHLCV

_OPS: Dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def symbol_metrics(series: OHLCV) -> Optional[dict]:
    """Compute the screenable metric set for one symbol. ``None`` if too short."""
    if len(series) < 60:
        return None
    close = list(series.close)
    last = len(series) - 1
    ema20 = ind.ema(close, 20)[last]
    ema50 = ind.ema(close, 50)[last]
    rsi14 = ind.rsi(close, 14)[last]
    atrp = ind.atr_percent(series, 14)[last]
    rvol = ind.relative_volume(series)[last]
    price = close[last]
    adv = sum(series.close[i] * series.volume[i] for i in range(max(0, last - 19), last + 1)) / min(20, len(series))
    ret_1m = (close[last] / close[last - 21] - 1.0) * 100 if last >= 21 else None
    ret_3m = (close[last] / close[last - 63] - 1.0) * 100 if last >= 63 else None
    high = max(series.high)
    dist_from_high = (price / high - 1.0) * 100 if high else None
    return {
        "price": round(price, 4),
        "ema20": round(ema20, 4) if ema20 else None,
        "ema50": round(ema50, 4) if ema50 else None,
        "rsi14": round(rsi14, 2) if rsi14 else None,
        "atr_pct": round(atrp, 3) if atrp else None,
        "rvol": round(rvol, 2) if rvol else None,
        "adv_dollar": round(adv, 0),
        "ret_1m_pct": round(ret_1m, 2) if ret_1m is not None else None,
        "ret_3m_pct": round(ret_3m, 2) if ret_3m is not None else None,
        "dist_from_high_pct": round(dist_from_high, 2) if dist_from_high is not None else None,
        "above_ema50": bool(ema50 and price > ema50),
        "above_ema20": bool(ema20 and price > ema20),
    }


def _passes(metrics: dict, filters: List[dict], combine: str) -> bool:
    """Evaluate filter predicates. Each filter: {field, op, value}. NOT via 'negate'."""
    results = []
    for f in filters:
        field, op, value = f["field"], f["op"], f["value"]
        mv = metrics.get(field)
        if mv is None:
            results.append(False)
            continue
        if isinstance(value, bool) or op in ("==", "!="):
            ok = _OPS[op](mv, value)
        else:
            ok = _OPS[op](mv, value)
        if f.get("negate"):
            ok = not ok
        results.append(ok)
    return all(results) if combine == "all" else any(results)


def liquidity_flags(metrics: dict, quote: Optional[dict] = None, min_adv: float = 1e6, min_price: float = 5.0) -> List[str]:
    flags = []
    if metrics.get("adv_dollar", 0) < min_adv:
        flags.append(f"thin liquidity: ADV ${metrics['adv_dollar']:,.0f} < ${min_adv:,.0f}")
    if metrics.get("price", 0) < min_price:
        flags.append(f"low price: ${metrics['price']} < ${min_price} (spread/borrow risk)")
    if quote and quote.get("spread_bps") is not None and quote["spread_bps"] > 25:
        flags.append(f"wide spread: {quote['spread_bps']} bps")
    return flags


def run_screen(
    symbols: List[str],
    filters: Optional[List[dict]] = None,
    provider: Optional[DataProvider] = None,
    combine: str = "all",
    rank_by: Optional[Dict[str, float]] = None,
    timeframe: str = "1d",
    lookback: int = 250,
    limit: Optional[int] = None,
) -> dict:
    """Screen ``symbols`` against ``filters``, ranked by a transparent composite.

    ``rank_by`` maps metric field -> weight for the composite score (default:
    momentum + trend). Every result carries the metrics that earned its rank and
    any liquidity flags.
    """
    provider = provider or SyntheticProvider()
    filters = filters or []
    rank_by = rank_by or {"ret_3m_pct": 1.0, "ret_1m_pct": 0.5, "rvol": 0.3}

    rows = []
    errors = []
    for sym in symbols:
        try:
            series = provider.get_ohlcv(sym, timeframe, lookback)
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": sym, "error": str(e)})
            continue
        m = symbol_metrics(series)
        if m is None:
            errors.append({"symbol": sym, "error": "insufficient history"})
            continue
        if filters and not _passes(m, filters, combine):
            continue
        score = sum(w * (m.get(field) or 0.0) for field, w in rank_by.items())
        reasons = [
            f"{field}={m.get(field)}" for field in rank_by if m.get(field) is not None
        ]
        try:
            q = None
            quote = provider.get_quote(sym)
            q = {"spread_bps": round(quote.spread_bps, 2) if quote.spread_bps is not None else None}
        except Exception:  # noqa: BLE001
            q = None
        rows.append({
            "symbol": sym,
            "composite_score": round(score, 3),
            "metrics": m,
            "rank_reasons": reasons,
            "liquidity_flags": liquidity_flags(m, q),
        })

    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    if limit:
        rows = rows[:limit]
    return {
        "criteria": {"filters": filters, "combine": combine, "rank_by": rank_by},
        "matched": len(rows),
        "results": rows,
        "errors": errors,
        "simulated": getattr(provider, "simulated", False),
        "disclaimer": "Educational analysis, not financial advice.",
    }
