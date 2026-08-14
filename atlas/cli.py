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
from .data import CSVProvider, SyntheticProvider
from .indicators import ema
from .tools import ToolRegistry


def _registry(args) -> ToolRegistry:
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
    p.add_argument("--csv", help="directory of <SYMBOL>_<TF>.csv real-data files")
    p.add_argument("--seed", type=int, default=42, help="synthetic-feed seed")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="full workup")
    pa.add_argument("symbol")
    pa.add_argument("--timeframe", default="1d")
    pa.add_argument("--lookback", type=int, default=300)
    pa.add_argument("--benchmark", default=None)

    ps = sub.add_parser("signal", help="risk-defined trade plan")
    ps.add_argument("symbol")
    ps.add_argument("--entry", type=float, required=True)
    ps.add_argument("--stop", type=float, required=True)
    ps.add_argument("--targets", required=True, help="comma-separated target prices")
    ps.add_argument("--direction", default="long", choices=["long", "short"])
    ps.add_argument("--equity", type=float, default=100_000)
    ps.add_argument("--risk-pct", type=float, default=0.01)

    pb = sub.add_parser("backtest", help="EMA-cross demo backtest")
    pb.add_argument("symbol")
    pb.add_argument("--timeframe", default="1d")
    pb.add_argument("--lookback", type=int, default=750)
    pb.add_argument("--fast", type=int, default=20)
    pb.add_argument("--slow", type=int, default=50)

    pc = sub.add_parser("screen", help="screen a universe (transparent criteria)")
    pc.add_argument("symbols", help="comma-separated symbols")
    pc.add_argument("--above-ema50", action="store_true", help="require price > EMA50")
    pc.add_argument("--limit", type=int, default=10)

    pd = sub.add_parser("portfolio", help="optimize weights over a universe")
    pd.add_argument("symbols", help="comma-separated symbols")
    pd.add_argument("--objective", default="min_variance",
                    choices=["equal_weight", "inverse_vol", "min_variance", "max_sharpe"])
    pd.add_argument("--max-weight", type=float, default=None)
    pd.add_argument("--benchmark", default=None)

    args = p.parse_args(argv)
    reg = _registry(args)

    if args.cmd == "analyze":
        out = analyze(args.symbol, registry=reg, timeframe=args.timeframe,
                      lookback=args.lookback, benchmark=args.benchmark)
    elif args.cmd == "signal":
        targets = [float(x) for x in args.targets.split(",")]
        out = build_signal(args.symbol, args.entry, args.stop, targets, args.direction,
                           args.equity, args.risk_pct)
    elif args.cmd == "backtest":
        fetched = reg.get_ohlcv(args.symbol, args.timeframe, args.lookback)
        if "error" in fetched:
            print(json.dumps(fetched, indent=2))
            return 1
        series = fetched["_series"]
        res = run_backtest(series, _ema_cross_signal(args.fast, args.slow))
        out = res.to_dict()
        out["verdict"] = verdict(res.metrics, len(res.trades))
        out["data_is_simulated"] = fetched["provenance"].get("simulated", False)
    elif args.cmd == "screen":
        symbols = [s.strip() for s in args.symbols.split(",")]
        filters = [{"field": "above_ema50", "op": "==", "value": True}] if args.above_ema50 else None
        out = reg.run_screen(symbols, filters=filters, limit=args.limit)
    elif args.cmd == "portfolio":
        symbols = [s.strip() for s in args.symbols.split(",")]
        out = reg.optimize_portfolio(symbols, objective=args.objective,
                                     max_weight=args.max_weight, benchmark=args.benchmark)
    else:  # pragma: no cover
        p.error("unknown command")
        return 2

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
