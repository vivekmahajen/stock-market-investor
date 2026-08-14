"""Function-calling tool registry (Section 3 contract).

Wraps the compute layer in the tool signatures the ATLAS system prompt expects,
returning JSON-serialisable dicts that always carry data provenance. A missing
capability degrades gracefully (returns an ``error`` field) rather than
fabricating a result.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import indicators as ind
from . import levels as lvl
from . import patterns as pat
from . import seasonality as seas
from .backtest import run_backtest, verdict
from .data import DataProvider, SyntheticProvider
from .types import OHLCV, Provenance


class ToolRegistry:
    """Holds a data provider and exposes the Section 3 tools as methods."""

    def __init__(self, provider: Optional[DataProvider] = None):
        self.provider = provider or SyntheticProvider()

    # --- market data -----------------------------------------------------
    def get_ohlcv(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> dict:
        try:
            series = self.provider.get_ohlcv(symbol, timeframe, lookback)
        except Exception as e:  # noqa: BLE001 - surface tool errors honestly
            return {"error": f"get_ohlcv failed: {e}"}
        prov = self.provider.provenance("get_ohlcv", symbol, timeframe, str(lookback))
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": len(series),
            "asof": series.asof.isoformat() if series.asof else None,
            "close": list(series.close),
            "provenance": prov.to_dict(),
            "_series": series,  # in-process handle; strip before serialising
        }

    def get_quote(self, symbol: str) -> dict:
        try:
            q = self.provider.get_quote(symbol)
        except Exception as e:  # noqa: BLE001
            return {"error": f"get_quote failed: {e}"}
        prov = self.provider.provenance("get_quote", symbol, None, None)
        return {
            "symbol": symbol,
            "last": q.last,
            "bid": q.bid,
            "ask": q.ask,
            "spread_bps": round(q.spread_bps, 2) if q.spread_bps is not None else None,
            "ts": q.ts.isoformat(),
            "staleness_seconds": round(q.staleness_seconds(), 1),
            "provenance": prov.to_dict(),
        }

    # --- compute ---------------------------------------------------------
    def compute_indicators(self, series: OHLCV, specs: Optional[List[str]] = None) -> dict:
        specs = specs or ["ema20", "ema50", "rsi14", "macd", "atr14", "bbands", "adx"]
        close = list(series.close)
        out: Dict[str, object] = {}
        for spec in specs:
            if spec == "ema20":
                out[spec] = _last(ind.ema(close, 20))
            elif spec == "ema50":
                out[spec] = _last(ind.ema(close, 50))
            elif spec == "ema200":
                out[spec] = _last(ind.ema(close, 200))
            elif spec == "rsi14":
                out[spec] = _last(ind.rsi(close, 14))
            elif spec == "macd":
                m = ind.macd(close)
                out[spec] = {"macd": _last(m["macd"]), "signal": _last(m["signal"]), "hist": _last(m["hist"])}
            elif spec == "atr14":
                out[spec] = _last(ind.atr(series, 14))
            elif spec == "bbands":
                b = ind.bollinger_bands(close)
                out[spec] = {"upper": _last(b["upper"]), "middle": _last(b["middle"]), "lower": _last(b["lower"]), "pct_b": _last(b["pct_b"])}
            elif spec == "adx":
                out[spec] = _last(ind.adx(series)["adx"])
            elif spec == "obv":
                out[spec] = _last(ind.obv(series))
            elif spec == "rvol":
                out[spec] = _last(ind.relative_volume(series))
            else:
                out[spec] = {"error": f"unknown indicator spec '{spec}'"}
        return out

    def detect_levels(self, series: OHLCV) -> dict:
        return lvl.detect_levels(series)

    def detect_patterns(self, series: OHLCV, families: Optional[List[str]] = None) -> List[dict]:
        return pat.latest_patterns(series)

    def compute_seasonality(self, series: OHLCV, granularity: str = "month") -> dict:
        return seas.compute_seasonality(series, granularity)

    def run_backtest(self, series: OHLCV, signal_fn, **kwargs) -> dict:
        res = run_backtest(series, signal_fn, **kwargs)
        d = res.to_dict()
        d["verdict"] = verdict(res.metrics, len(res.trades))
        return d


def _last(seq):
    for v in reversed(seq):
        if v is not None:
            return round(v, 4)
    return None
