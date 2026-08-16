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
from .alerts import AlertStore
from .backtest import run_backtest, verdict
from .chart_patterns import detect_classical
from .data import DataProvider, SyntheticProvider
from .fibonacci import auto_fibonacci
from .harmonics import detect_harmonics
from .portfolio import optimize_portfolio
from .screen import run_screen
from .types import OHLCV, Provenance


class ToolRegistry:
    """Holds a data provider and exposes the Section 3 tools as methods."""

    def __init__(self, provider: Optional[DataProvider] = None, alert_store: Optional[AlertStore] = None):
        self.provider = provider or SyntheticProvider()
        self.alerts = alert_store or AlertStore()

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

    def detect_patterns(self, series: OHLCV, families: Optional[List[str]] = None) -> dict:
        """Detect patterns across requested families (Section 5).

        Families: ``candlestick``, ``classical``, ``harmonic``. Defaults to all.
        """
        families = families or ["candlestick", "classical", "harmonic"]
        out: Dict[str, object] = {}
        if "candlestick" in families:
            out["candlestick"] = pat.latest_patterns(series, lookback=5)
        if "classical" in families:
            out["classical"] = detect_classical(series)
        if "harmonic" in families:
            out["harmonic"] = detect_harmonics(series)
        return out

    def fibonacci(self, series: OHLCV) -> Optional[dict]:
        return auto_fibonacci(series)

    def compute_seasonality(self, series: OHLCV, granularity: str = "month") -> dict:
        return seas.compute_seasonality(series, granularity)

    def run_backtest(self, series: OHLCV, signal_fn, **kwargs) -> dict:
        res = run_backtest(series, signal_fn, **kwargs)
        d = res.to_dict()
        d["verdict"] = verdict(res.metrics, len(res.trades))
        return d

    def run_screen(self, symbols: List[str], filters=None, **kwargs) -> dict:
        return run_screen(symbols, filters=filters, provider=self.provider, **kwargs)

    def optimize_portfolio(self, symbols: List[str], objective: str = "min_variance",
                           timeframe: str = "1d", lookback: int = 300, benchmark: Optional[str] = None,
                           **kwargs) -> dict:
        series_by_symbol = {}
        for s in symbols:
            f = self.get_ohlcv(s, timeframe, lookback)
            if "_series" in f:
                series_by_symbol[s] = f["_series"]
        if len(series_by_symbol) < 2:
            return {"error": "Need at least two symbols with sufficient history."}
        bench_series = None
        if benchmark:
            bf = self.get_ohlcv(benchmark, timeframe, lookback)
            bench_series = bf.get("_series")
        res = optimize_portfolio(series_by_symbol, objective=objective, benchmark=bench_series, **kwargs)
        out = res.to_dict()
        out["simulated"] = getattr(self.provider, "simulated", False)
        return out

    def get_fundamentals(self, symbol: str) -> dict:
        if not hasattr(self.provider, "get_fundamentals"):
            return {"error": f"provider '{getattr(self.provider, 'source', '?')}' has no fundamentals feed"}
        try:
            overview = self.provider.get_fundamentals(symbol)
        except Exception as e:  # noqa: BLE001
            return {"error": f"get_fundamentals failed: {e}"}
        return {"overview": overview, "provenance": self.provider.provenance("get_fundamentals", symbol, None, None).to_dict()}

    def get_news_sentiment(self, symbol: str, window: int = 50) -> dict:
        if not hasattr(self.provider, "get_news_sentiment"):
            return {"error": f"provider '{getattr(self.provider, 'source', '?')}' has no news feed"}
        try:
            news = self.provider.get_news_sentiment(symbol, window)
        except Exception as e:  # noqa: BLE001
            return {"error": f"get_news_sentiment failed: {e}"}
        return {"news": news, "provenance": self.provider.provenance("get_news_sentiment", symbol, None, None).to_dict()}

    def create_alert(self, symbol: str, condition: dict, channel: str = "log", note: str = "") -> dict:
        try:
            alert = self.alerts.create_alert(symbol, condition, channel, note)
        except ValueError as e:
            return {"error": str(e)}
        return alert.to_dict()

    def check_alerts(self) -> List[dict]:
        out = []
        for alert in self.alerts.list_alerts(active_only=True):
            f = self.get_ohlcv(alert.symbol, "1d", 250)
            if "_series" in f:
                out.append(self.alerts.evaluate(alert, f["_series"]))
        return out


def _last(seq):
    for v in reversed(seq):
        if v is not None:
            return round(v, 4)
    return None
