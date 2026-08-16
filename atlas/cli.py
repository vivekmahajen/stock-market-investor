"""Minimal CLI to exercise the ATLAS compute layer against the synthetic feed.

    python -m atlas.cli analyze AAPL
    python -m atlas.cli signal AAPL --entry 100 --stop 95 --targets 110,120
    python -m atlas.cli backtest AAPL

The synthetic provider is used by default; its output is flagged as SIMULATED.
Point ``--csv <dir>`` at a directory of ``<SYMBOL>_<TF>.csv`` files for real data.
"""
from __future__ import annotations

import argparse
import json
import sys

from .analysis import analyze, build_signal
from .backtest import run_backtest, verdict
from .data import AlphaVantageProvider, CSVProvider, StooqProvider, SyntheticProvider
from .indicators import ema
from .tools import ToolRegistry


def _registry(args) -> ToolRegistry:
    if getattr(args, "alpha_vantage", False):
        return ToolRegistry(AlphaVantageProvider(
            api_key=getattr(args, "api_key", None),
            premium=getattr(args, "premium", False),
        ))
    if getattr(args, "stooq", False):
        return ToolRegistry(StooqProvider())
    if getattr(args, "csv", None):
        return ToolRegistry(CSVProvider(args.csv))
    return ToolRegistry(SyntheticProvider(seed=args.seed))


def _ema_cross_signal(fast: int = 20, slow: int = 50):
    def fn(series, i):
        c = list(series.close[: i + 1])
        ef, es = ema(c, fast), ema(c, slow)
        if ef[i] is None or es[i] is None:
            return 0
        return 1 if ef[i] > es[i] else -1

    return fn


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="atlas", description="ATLAS compute-layer CLI")
    # Shared data-source flags, added to every subcommand via `parents=` so they
    # work whether placed before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--csv", help="directory of <SYMBOL>_<TF>.csv real-data files")
    common.add_argument("--stooq", action="store_true", help="use live Stooq data (free, no key; EOD)")
    common.add_argument("--alpha-vantage", action="store_true",
                        help="use Alpha Vantage (needs a free API key)")
    common.add_argument("--api-key", default=None,
                        help="Alpha Vantage API key (else ALPHAVANTAGE_API_KEY env var)")
    common.add_argument("--premium", action="store_true",
                        help="Alpha Vantage premium key (enables full-history outputsize)")
    common.add_argument("--seed", type=int, default=42, help="synthetic-feed seed")
    common.add_argument("--format", choices=["json", "text"], default="json",
                        help="output format: json (default, machine-readable) or text")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", parents=[common], help="full workup")
    pa.add_argument("symbol")
    pa.add_argument("--timeframe", default="1d")
    pa.add_argument("--lookback", type=int, default=300)
    pa.add_argument("--benchmark", default=None)
    pa.add_argument("--fundamentals", action="store_true",
                    help="fetch fundamentals to score the fundamental factor (extra API call)")
    pa.add_argument("--sentiment", action="store_true",
                    help="fetch news to score the sentiment factor (extra API call)")
    pa.add_argument("--events", action="store_true",
                    help="check the earnings calendar for event risk (extra API call)")

    ps = sub.add_parser("signal", parents=[common], help="risk-defined trade plan")
    ps.add_argument("symbol")
    ps.add_argument("--entry", type=float, required=True)
    ps.add_argument("--stop", type=float, required=True)
    ps.add_argument("--targets", required=True, help="comma-separated target prices")
    ps.add_argument("--direction", default="long", choices=["long", "short"])
    ps.add_argument("--equity", type=float, default=100_000)
    ps.add_argument("--risk-pct", type=float, default=0.01)
    ps.add_argument("--events", action="store_true",
                    help="check the earnings calendar before issuing the plan (extra API call)")

    pb = sub.add_parser("backtest", parents=[common], help="EMA-cross demo backtest")
    pb.add_argument("symbol")
    pb.add_argument("--timeframe", default="1d")
    pb.add_argument("--lookback", type=int, default=750)
    pb.add_argument("--fast", type=int, default=20)
    pb.add_argument("--slow", type=int, default=50)
    pb.add_argument("--robustness", default="none",
                    choices=["none", "split", "walkforward", "sensitivity", "subperiods"],
                    help="robustness check to run instead of a single backtest (§8)")

    pc = sub.add_parser("screen", parents=[common], help="screen a universe (transparent criteria)")
    pc.add_argument("symbols", help="comma-separated symbols")
    pc.add_argument("--above-ema50", action="store_true", help="require price > EMA50")
    pc.add_argument("--limit", type=int, default=10)

    pd = sub.add_parser("portfolio", parents=[common], help="optimize weights over a universe")
    pd.add_argument("symbols", help="comma-separated symbols")
    pd.add_argument("--objective", default="min_variance",
                    choices=["equal_weight", "inverse_vol", "min_variance", "max_sharpe"])
    pd.add_argument("--max-weight", type=float, default=None)
    pd.add_argument("--benchmark", default=None)

    pw = sub.add_parser("serve", help="launch the web dashboard")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8787)

    po = sub.add_parser("option", parents=[common], help="Black-Scholes price + greeks (+ implied vol)")
    po.add_argument("--spot", type=float, required=True)
    po.add_argument("--strike", type=float, required=True)
    po.add_argument("--dte", type=float, required=True, help="days to expiry")
    po.add_argument("--kind", default="call", choices=["call", "put"])
    po.add_argument("--rate", type=float, default=0.04, help="risk-free rate (annual)")
    po.add_argument("--div-yield", type=float, default=0.0)
    po.add_argument("--vol", type=float, default=None, help="volatility (to price)")
    po.add_argument("--price", type=float, default=None, help="market price (to imply vol)")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .web import serve
        serve(args.host, args.port)
        return 0

    reg = _registry(args)

    if args.cmd == "analyze":
        out = analyze(args.symbol, registry=reg, timeframe=args.timeframe,
                      lookback=args.lookback, benchmark=args.benchmark,
                      with_fundamentals=args.fundamentals, with_sentiment=args.sentiment,
                      with_events=args.events)
    elif args.cmd == "signal":
        targets = [float(x) for x in args.targets.split(",")]
        events = None
        if args.events:
            from datetime import datetime, timezone

            from .events import build_event_risk
            er = reg.get_earnings_calendar(args.symbol)
            if "error" in er:
                events = [{"type": "earnings", "error": er["error"]}]
            else:
                events = build_event_risk(er["earnings"], datetime.now(timezone.utc).date())
        out = build_signal(args.symbol, args.entry, args.stop, targets, args.direction,
                           args.equity, args.risk_pct, events=events)
    elif args.cmd == "backtest":
        fetched = reg.get_ohlcv(args.symbol, args.timeframe, args.lookback)
        if "error" in fetched:
            print(json.dumps(fetched, indent=2))
            return 1
        series = fetched["_series"]
        if args.robustness == "none":
            res = run_backtest(series, _ema_cross_signal(args.fast, args.slow))
            out = res.to_dict()
            out["verdict"] = verdict(res.metrics, len(res.trades))
        else:
            from .robustness import (parameter_sensitivity, sub_period_analysis,
                                     train_test_split, walk_forward)
            factory = lambda p: _ema_cross_signal(p["fast"], p["slow"])
            grid = {"fast": [10, 20, 30], "slow": [50, 100, 200]}
            if args.robustness == "split":
                out = train_test_split(series, _ema_cross_signal(args.fast, args.slow))
            elif args.robustness == "walkforward":
                out = walk_forward(series, factory, grid)
            elif args.robustness == "sensitivity":
                out = parameter_sensitivity(series, factory, grid)
            else:  # subperiods
                out = sub_period_analysis(series, _ema_cross_signal(args.fast, args.slow))
        out["data_is_simulated"] = fetched["provenance"].get("simulated", False)
    elif args.cmd == "screen":
        symbols = [s.strip() for s in args.symbols.split(",")]
        filters = [{"field": "above_ema50", "op": "==", "value": True}] if args.above_ema50 else None
        out = reg.run_screen(symbols, filters=filters, limit=args.limit)
    elif args.cmd == "portfolio":
        symbols = [s.strip() for s in args.symbols.split(",")]
        out = reg.optimize_portfolio(symbols, objective=args.objective,
                                     max_weight=args.max_weight, benchmark=args.benchmark)
    elif args.cmd == "option":
        from .options import option_analysis
        out = option_analysis(args.spot, args.strike, args.dte / 365.0, args.rate,
                              kind=args.kind, q=args.div_yield, sigma=args.vol, price=args.price)
    else:  # pragma: no cover
        p.error("unknown command")
        return 2

    if getattr(args, "format", "json") == "text":
        from .report import render
        text = render(args.cmd, out)
        print(text if text else json.dumps(out, indent=2, default=str))
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
