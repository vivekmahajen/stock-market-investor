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
            elif spec == "cci":
                out[spec] = _last(ind.cci(series))
            elif spec == "williams_r":
                out[spec] = _last(ind.williams_r(series))
            elif spec == "mfi":
                out[spec] = _last(ind.mfi(series))
            elif spec == "stoch_rsi":
                s = ind.stoch_rsi(close)
                out[spec] = {"k": _last(s["k"]), "d": _last(s["d"])}
            elif spec == "supertrend":
                s = ind.supertrend(series)
                out[spec] = {"supertrend": _last(s["supertrend"]), "direction": _last(s["direction"])}
            elif spec == "ichimoku":
                ich = ind.ichimoku(series)
                out[spec] = {k: _last(v) for k, v in ich.items()}
            elif spec == "keltner":
                k = ind.keltner_channels(series)
                out[spec] = {"upper": _last(k["upper"]), "middle": _last(k["middle"]), "lower": _last(k["lower"])}
            elif spec == "donchian":
                d = ind.donchian_channels(series)
                out[spec] = {"upper": _last(d["upper"]), "middle": _last(d["middle"]), "lower": _last(d["lower"])}
            elif spec == "cmf":
                out[spec] = _last(ind.cmf(series))
            elif spec == "hist_vol":
                out[spec] = _last(ind.historical_volatility(close))
            elif spec == "choppiness":
                out[spec] = _last(ind.choppiness_index(series))
            elif spec == "psar":
                out[spec] = _last(ind.parabolic_sar(series))
            elif spec == "hma":
                out[spec] = _last(ind.hma(close, 16))
            elif spec == "vwma":
                out[spec] = _last(ind.vwma(series))
            elif spec == "volume_profile":
                out[spec] = ind.volume_profile(series)
            else:
                out[spec] = {"error": f"unknown indicator spec '{spec}'"}
        return out

    def detect_levels(self, series: OHLCV) -> dict:
        return lvl.detect_levels(series)

    def detect_structure(self, series: OHLCV) -> dict:
        """Trendlines, channels, pivot points, gaps, and volume-profile levels (§4)."""
        return {
            "trendlines": lvl.detect_trendlines(series),
            "channel": lvl.detect_channels(series),
            "pivots": lvl.pivot_points(series),
            "gaps": lvl.detect_gaps(series),
            "volume_profile": lvl.volume_profile_levels(series),
        }

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
        from .portfolio import benchmark_comparison, position_roles
        out["roles"] = position_roles(series_by_symbol, res.weights, bench_series)
        if bench_series is not None:
            out["benchmark_comparison"] = benchmark_comparison(series_by_symbol, res.weights, bench_series)
        out["simulated"] = getattr(self.provider, "simulated", False)
        out["_series_by_symbol"] = series_by_symbol  # in-process handle for rebalance suggestions
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

    def get_earnings_calendar(self, symbol: str, horizon: str = "3month") -> dict:
        if not hasattr(self.provider, "get_earnings_calendar"):
            return {"error": f"provider '{getattr(self.provider, 'source', '?')}' has no earnings calendar"}
        try:
            rows = self.provider.get_earnings_calendar(symbol, horizon)
        except Exception as e:  # noqa: BLE001
            return {"error": f"get_earnings_calendar failed: {e}"}
        return {"earnings": rows, "provenance": self.provider.provenance("get_earnings_calendar", symbol, None, None).to_dict()}

    def get_options_chain(self, symbol: str, expiries_days=(30, 60, 90), sigma: Optional[float] = None,
                          timeframe: str = "1d", lookback: int = 200, n_strikes: int = 5) -> dict:
        """Model-generated options chain priced with Black-Scholes at realized vol."""
        from .options import build_chain

        f = self.get_ohlcv(symbol, timeframe, lookback)
        if "error" in f:
            return {"error": f["error"]}
        series = f["_series"]
        if sigma is None:
            hv = ind.historical_volatility(list(series.close), 20)
            last_hv = next((v for v in reversed(hv) if v is not None), None)
            sigma = (last_hv / 100.0) if last_hv else 0.3
        chain = build_chain(series.close[-1], expiries_days, sigma, n_strikes=n_strikes)
        chain["symbol"] = symbol
        chain["simulated"] = f["provenance"].get("simulated", False)
        chain["sigma_source"] = "20-bar realized volatility (proxy for implied vol)"
        return chain

    def get_calendar(self, symbol: str, horizon: str = "3month") -> dict:
        """Unified calendar: upcoming earnings + dividends + splits (§3/§12)."""
        out: dict = {"symbol": symbol, "earnings": [], "dividends": [], "splits": []}
        errors = []
        er = self.get_earnings_calendar(symbol, horizon)
        if "earnings" in er:
            out["earnings"] = er["earnings"]
        else:
            errors.append(er.get("error"))
        for name, meth in (("dividends", "get_dividends"), ("splits", "get_splits")):
            if hasattr(self.provider, meth):
                try:
                    out[name] = getattr(self.provider, meth)(symbol)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{name}: {e}")
            else:
                errors.append(f"{name}: provider has no feed")
        out["errors"] = [e for e in errors if e]
        return out

    def paper_trade(self, order: dict) -> dict:
        """Submit a simulated order to the registry's paper broker (never live)."""
        if not hasattr(self, "broker"):
            from .paper import PaperBroker
            self.broker = PaperBroker()
        try:
            fill = self.broker.submit(order["symbol"], order["side"], float(order["qty"]), float(order["price"]))
        except (KeyError, ValueError) as e:
            return {"error": f"paper_trade failed: {e}"}
        return {"fill": fill, "account": self.broker.to_dict()}

    def create_alert(self, symbol: str, condition: dict, channel: str = "log", note: str = "") -> dict:
        try:
            alert = self.alerts.create_alert(symbol, condition, channel, note)
        except ValueError as e:
            return {"error": str(e)}
        return alert.to_dict()

    # --- forecasting, the daily report, and the prediction store (§20-22) ---
    def get_universe(self, name: str = "nasdaq10", refresh: bool = False, limit: int = 10) -> dict:
        """Resolve a named universe (e.g. the NASDAQ top 10) with provenance."""
        from .universe import resolve_universe

        return resolve_universe(name, registry=self, refresh=refresh, limit=limit)

    def forecast_price(self, symbol: str, horizon_days: int = 30, method: str = "drift",
                       timeframe: str = "1d", lookback: int = 400,
                       with_skill: bool = True) -> dict:
        """Horizon price distribution for ``symbol`` plus its walk-forward skill.

        The returned ``forecast_price`` is the median of a modelled distribution.
        ``skill`` reports whether that model has ever beaten a random walk on this
        symbol — read it before quoting the point forecast.
        """
        from .forecast import backtest_forecast, forecast

        fetched = self.get_ohlcv(symbol, timeframe, lookback)
        if "error" in fetched:
            return {"symbol": symbol, "error": fetched["error"]}
        series = fetched["_series"]
        out = forecast(series, horizon_days=horizon_days, method=method)
        if "error" in out:
            return out
        out["simulated"] = fetched["provenance"].get("simulated", False)
        out["provenance"] = fetched["provenance"]
        if with_skill:
            out["skill"] = backtest_forecast(series, horizon_days=horizon_days, method=method)
        return out

    def compare_forecast_methods(self, symbol: str, horizon_days: int = 30,
                                 timeframe: str = "1d", lookback: int = 750) -> dict:
        """Score every forecast method over identical origins so the ranking is fair."""
        from .forecast import compare_methods

        fetched = self.get_ohlcv(symbol, timeframe, lookback)
        if "error" in fetched:
            return {"symbol": symbol, "error": fetched["error"]}
        return compare_methods(fetched["_series"], horizon_days=horizon_days)

    def run_daily_report(self, universe: str = "nasdaq10", horizon_days: int = 30,
                         method: str = "drift", db_path: Optional[str] = None,
                         persist: bool = True, **kwargs) -> dict:
        """Run the daily forecast report over a universe and persist it (§20/§22)."""
        from .daily import run_daily

        return run_daily(registry=self, universe=universe, horizon_days=horizon_days,
                         method=method, db_path=db_path, persist=persist, **kwargs)

    def render_report(self, report: dict, fmt: str = "markdown") -> str:
        """Render a report envelope as ``text``, ``markdown`` or ``html``."""
        from .daily import render_daily

        return render_daily(report, fmt)

    def query_predictions(self, run_id: Optional[int] = None, symbol: Optional[str] = None,
                          resolved: Optional[bool] = None, db_path: Optional[str] = None,
                          limit: int = 200) -> dict:
        """Read stored predictions (joined to outcomes) out of the table."""
        from .store import DEFAULT_DB, PredictionStore

        with PredictionStore(db_path or DEFAULT_DB) as store:
            rows = store.predictions(run_id=run_id, symbol=symbol, resolved=resolved, limit=limit)
            return {"count": len(rows), "rows": rows, "stats": store.stats()}

    def report_from_store(self, run_id: Optional[int] = None, universe: Optional[str] = None,
                          db_path: Optional[str] = None, fmt: Optional[str] = None) -> dict:
        """Regenerate a stored report (optionally rendered) from the table alone."""
        from .daily import render_daily, report_from_store
        from .store import DEFAULT_DB, PredictionStore

        with PredictionStore(db_path or DEFAULT_DB) as store:
            rep = report_from_store(store, run_id=run_id, universe=universe)
            if fmt and "error" not in rep:
                rep["rendered"] = render_daily(rep, fmt)
                rep["rendered_format"] = fmt
            return rep

    def resolve_predictions(self, asof: Optional[str] = None, db_path: Optional[str] = None,
                            timeframe: str = "1d", lookback: int = 400) -> dict:
        """Score every prediction whose horizon has elapsed against the realised close."""
        from .store import DEFAULT_DB, PredictionStore

        with PredictionStore(db_path or DEFAULT_DB) as store:
            return store.resolve_due(self, asof=asof, timeframe=timeframe, lookback=lookback)

    def forecast_accuracy(self, symbol: Optional[str] = None, horizon_days: Optional[int] = None,
                          db_path: Optional[str] = None) -> dict:
        """Realised accuracy over resolved predictions, plus the per-symbol leaderboard."""
        from .store import DEFAULT_DB, PredictionStore

        with PredictionStore(db_path or DEFAULT_DB) as store:
            return {
                "overall": store.accuracy(symbol=symbol, horizon_days=horizon_days),
                "by_symbol": store.leaderboard(),
                "stats": store.stats(),
            }

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
