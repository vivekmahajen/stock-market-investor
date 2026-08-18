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
    # Reports carry Unicode (⚠ banners, en/em dashes). Windows consoles default
    # to cp1252, which can't encode them and crashes on print/redirect — force
    # UTF-8 on stdout/stderr so `atlas ... > report.html` works everywhere.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

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

    ps = sub.add_parser("signal", parents=[common],
                        help="risk-defined trade plan (auto-proposed if entry/stop/targets omitted)")
    ps.add_argument("symbol")
    ps.add_argument("--entry", type=float, default=None)
    ps.add_argument("--stop", type=float, default=None)
    ps.add_argument("--targets", default=None, help="comma-separated target prices")
    ps.add_argument("--direction", default="long", choices=["long", "short"])
    ps.add_argument("--equity", type=float, default=100_000)
    ps.add_argument("--risk-pct", type=float, default=0.01)
    ps.add_argument("--timeframe", default="1d")
    ps.add_argument("--lookback", type=int, default=300)
    ps.add_argument("--events", action="store_true",
                    help="check the earnings calendar before issuing the plan (extra API call)")
    ps.add_argument("--journal", default=None, help="log this signal to a calibration journal file")

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

    psc = sub.add_parser("score", parents=[common], help="ATLAS Score only")
    psc.add_argument("symbol")
    psc.add_argument("--timeframe", default="1d")
    psc.add_argument("--lookback", type=int, default=300)
    psc.add_argument("--benchmark", default=None)
    psc.add_argument("--fundamentals", action="store_true")
    psc.add_argument("--sentiment", action="store_true")
    psc.add_argument("--study", action="store_true",
                     help="add in-sample probabilistic framing by score band")

    pse = sub.add_parser("seasonality", parents=[common], help="calendar-bucketed return stats")
    pse.add_argument("symbol")
    pse.add_argument("--granularity", default="month", choices=["month", "weekday"])
    pse.add_argument("--lookback", type=int, default=750)

    pwt = sub.add_parser("watch", parents=[common], help="score a watchlist, ranked")
    pwt.add_argument("symbols", help="comma-separated symbols")
    pwt.add_argument("--timeframe", default="1d")
    pwt.add_argument("--lookback", type=int, default=300)

    pex = sub.add_parser("explain", parents=[common], help="full narrative workup + auto trade plan")
    pex.add_argument("symbol")
    pex.add_argument("--timeframe", default="1d")
    pex.add_argument("--lookback", type=int, default=300)
    pex.add_argument("--benchmark", default=None)

    prb = sub.add_parser("rebalance", parents=[common], help="rebalancing plan vs current weights")
    prb.add_argument("symbols", help="comma-separated symbols")
    prb.add_argument("--current", required=True, help="comma-separated current weights (aligned to symbols)")
    prb.add_argument("--objective", default="min_variance",
                     choices=["equal_weight", "inverse_vol", "min_variance", "max_sharpe"])
    prb.add_argument("--drift-band", type=float, default=0.05)
    prb.add_argument("--capital", type=float, default=None)

    pal = sub.add_parser("alert", parents=[common], help="create / list / check alerts")
    pal.add_argument("action", choices=["add", "list", "check", "remove"])
    pal.add_argument("symbol", nargs="?")
    pal.add_argument("--kind", default="price_above")
    pal.add_argument("--value", type=float, default=None)
    pal.add_argument("--period", type=int, default=14)
    pal.add_argument("--mult", type=float, default=1.0)
    pal.add_argument("--id", dest="alert_id", default=None)
    pal.add_argument("--store", default="atlas_alerts.json")

    pch = sub.add_parser("chain", parents=[common], help="model-generated options chain (Black-Scholes)")
    pch.add_argument("symbol")
    pch.add_argument("--dtes", default="30,60,90", help="comma-separated days-to-expiry")
    pch.add_argument("--vol", type=float, default=None, help="override sigma (else realized vol)")
    pch.add_argument("--strikes", type=int, default=5, help="strikes each side of spot")

    pcal = sub.add_parser("calendar", parents=[common], help="earnings + dividends + splits")
    pcal.add_argument("symbol")

    pmtf = sub.add_parser("mtf", parents=[common], help="multi-timeframe alignment")
    pmtf.add_argument("symbol")
    pmtf.add_argument("--timeframes", default="1d,1w", help="comma-separated timeframes")
    pmtf.add_argument("--lookback", type=int, default=300)

    pj = sub.add_parser("journal", parents=[common], help="signal calibration journal")
    pj.add_argument("action", choices=["resolve", "metrics", "list"])
    pj.add_argument("symbol", nargs="?")
    pj.add_argument("--store", default="atlas_journal.json")
    pj.add_argument("--lookback", type=int, default=400)

    pp = sub.add_parser("paper", parents=[common], help="paper-trading ledger (simulated)")
    pp.add_argument("action", choices=["buy", "sell", "status", "reset"])
    pp.add_argument("symbol", nargs="?")
    pp.add_argument("--qty", type=float, default=None)
    pp.add_argument("--price", type=float, default=None)
    pp.add_argument("--cash", type=float, default=100_000)
    pp.add_argument("--store", default="atlas_paper.json")

    po = sub.add_parser("option", parents=[common], help="Black-Scholes price + greeks (+ implied vol)")
    po.add_argument("--spot", type=float, required=True)
    po.add_argument("--strike", type=float, required=True)
    po.add_argument("--dte", type=float, required=True, help="days to expiry")
    po.add_argument("--kind", default="call", choices=["call", "put"])
    po.add_argument("--rate", type=float, default=0.04, help="risk-free rate (annual)")
    po.add_argument("--div-yield", type=float, default=0.0)
    po.add_argument("--vol", type=float, default=None, help="volatility (to price)")
    po.add_argument("--price", type=float, default=None, help="market price (to imply vol)")

    pdr = sub.add_parser("daily", parents=[common],
                         help="ATLAS Daily Report: NASDAQ-top-10 30-day forecast")
    pdr.add_argument("action", nargs="?", default="run",
                     choices=["run", "resolve", "accuracy", "replay"],
                     help="run the report (default), resolve elapsed predictions, "
                          "show realised accuracy, or replay a stored run")
    pdr.add_argument("--universe", default="nasdaq10", help="universe key (default nasdaq10)")
    pdr.add_argument("--horizon", type=int, default=30, help="forecast horizon in calendar days")
    pdr.add_argument("--method", default="drift", choices=["drift", "zero_drift"],
                     help="forecast method (drift default; zero_drift = random-walk baseline)")
    pdr.add_argument("--lookback", type=int, default=400, help="bars of history to fetch")
    pdr.add_argument("--refresh", action="store_true",
                     help="live-rerank the universe by market cap (needs a fundamentals feed)")
    pdr.add_argument("--no-events", action="store_true", help="skip the earnings-calendar check")
    pdr.add_argument("--store", default="atlas_predictions.json",
                     help="prediction-store JSON path (persists forecasts & outcomes)")
    pdr.add_argument("--no-store", action="store_true", help="do not persist predictions")
    pdr.add_argument("--asof", default=None, help="override the run date (YYYY-MM-DD, for backfills)")
    pdr.add_argument("--run-id", default=None, help="replay: which stored run to reconstruct")
    pdr.add_argument("--report-format", default="text", choices=["text", "markdown", "html", "json"],
                     help="rendered artefact format (default text)")

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
        if args.entry is None or args.stop is None or args.targets is None:
            # Auto-propose the full plan from the analysis.
            from .analysis import propose_signal
            out = propose_signal(args.symbol, registry=reg, account_equity=args.equity,
                                 risk_pct=args.risk_pct, timeframe=args.timeframe,
                                 lookback=args.lookback, with_events=args.events)
        else:
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
        if args.journal:
            from .journal import SignalJournal
            rec = SignalJournal(args.journal).record(out)
            out["journaled"] = bool(rec)
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
        out.pop("_series_by_symbol", None)
    elif args.cmd == "score":
        full = analyze(args.symbol, registry=reg, timeframe=args.timeframe, lookback=args.lookback,
                       benchmark=args.benchmark, with_fundamentals=args.fundamentals,
                       with_sentiment=args.sentiment, with_score_study=args.study)
        if "error" in full:
            out = full
        else:
            out = {k: full[k] for k in ("symbol", "atlas_score", "subscores", "score_label",
                                        "score_horizon", "top_contributors", "score_dynamics",
                                        "score_probabilistic", "regime", "notes",
                                        "data_is_simulated", "disclaimer") if k in full}
    elif args.cmd == "seasonality":
        fetched = reg.get_ohlcv(args.symbol, "1d", args.lookback)
        if "error" in fetched:
            out = fetched
        else:
            out = reg.compute_seasonality(fetched["_series"], args.granularity)
    elif args.cmd == "watch":
        symbols = [s.strip() for s in args.symbols.split(",")]
        results = []
        for sym in symbols:
            a = analyze(sym, registry=reg, timeframe=args.timeframe, lookback=args.lookback)
            if "error" in a:
                results.append({"symbol": sym, "error": a["error"]})
            else:
                results.append({"symbol": sym, "atlas_score": a["atlas_score"],
                                "label": a["score_label"], "regime": a["regime"]})
        results.sort(key=lambda r: r.get("atlas_score") or -1, reverse=True)
        out = {"results": results, "count": len(results)}
    elif args.cmd == "explain":
        from .analysis import propose_signal
        out = analyze(args.symbol, registry=reg, timeframe=args.timeframe,
                      lookback=args.lookback, benchmark=args.benchmark)
        if "error" not in out:
            out["_signal"] = propose_signal(args.symbol, registry=reg, timeframe=args.timeframe,
                                            lookback=args.lookback)
    elif args.cmd == "rebalance":
        from .portfolio import rebalance_plan
        symbols = [s.strip() for s in args.symbols.split(",")]
        weights = [float(x) for x in args.current.split(",")]
        if len(weights) != len(symbols):
            out = {"error": "number of --current weights must match number of symbols"}
        else:
            opt = reg.optimize_portfolio(symbols, objective=args.objective)
            if "error" in opt:
                out = opt
            else:
                from .portfolio import tax_aware_notes
                opt.pop("_series_by_symbol", None)
                current = dict(zip(symbols, weights))
                out = rebalance_plan(current, opt["weights"], drift_band=args.drift_band, capital=args.capital)
                out["target_weights"] = opt["weights"]
                out["roles"] = opt.get("roles")
                out["tax"] = tax_aware_notes(out["trades"])
    elif args.cmd == "alert":
        from .alerts import AlertStore
        store = AlertStore(args.store)
        reg.alerts = store
        if args.action == "add":
            cond = {"kind": args.kind}
            if args.value is not None:
                cond["value"] = args.value
            if args.kind in ("rsi_above", "rsi_below", "cross_above_ema", "cross_below_ema", "atr_move"):
                cond["period"] = args.period
            if args.kind == "atr_move":
                cond["mult"] = args.mult
            out = reg.create_alert(args.symbol, cond)
        elif args.action == "list":
            out = {"alerts": [a.to_dict() for a in store.list_alerts()]}
        elif args.action == "remove":
            out = {"removed": store.remove(args.alert_id)}
        else:  # check
            out = {"triggered": reg.check_alerts()}
    elif args.cmd == "chain":
        dtes = [int(x) for x in args.dtes.split(",")]
        out = reg.get_options_chain(args.symbol, expiries_days=dtes, sigma=args.vol, n_strikes=args.strikes)
    elif args.cmd == "calendar":
        out = reg.get_calendar(args.symbol)
    elif args.cmd == "mtf":
        from .analysis import multi_timeframe
        tfs = tuple(t.strip() for t in args.timeframes.split(","))
        out = multi_timeframe(args.symbol, registry=reg, timeframes=tfs, lookback=args.lookback)
    elif args.cmd == "journal":
        from .journal import SignalJournal
        j = SignalJournal(args.store)
        if args.action == "metrics":
            out = j.metrics()
        elif args.action == "list":
            out = {"records": j.records()}
        else:  # resolve
            if not args.symbol:
                out = {"error": "resolve needs a symbol"}
            else:
                fetched = reg.get_ohlcv(args.symbol, "1d", args.lookback)
                if "error" in fetched:
                    out = fetched
                else:
                    resolved = j.resolve(args.symbol, fetched["_series"])
                    out = {"resolved": resolved, "metrics": j.metrics()}
    elif args.cmd == "paper":
        from .paper import PaperBroker
        broker = PaperBroker(starting_cash=args.cash, path=args.store)
        if args.action in ("buy", "sell"):
            if not args.symbol or args.qty is None or args.price is None:
                out = {"error": "buy/sell need symbol, --qty and --price"}
            else:
                try:
                    fill = broker.submit(args.symbol, args.action, args.qty, args.price)
                    out = {"fill": fill, "account": broker.to_dict()}
                except ValueError as e:
                    out = {"error": str(e)}
        elif args.action == "reset":
            broker.reset()
            out = {"reset": True, "account": broker.to_dict()}
        else:  # status
            out = broker.to_dict(include_trades=True)
    elif args.cmd == "option":
        from .options import option_analysis
        out = option_analysis(args.spot, args.strike, args.dte / 365.0, args.rate,
                              kind=args.kind, q=args.div_yield, sigma=args.vol, price=args.price)
    elif args.cmd == "daily":
        from .daily import (forecast_accuracy, render_report, report_from_store,
                            resolve_predictions, run_daily_report)
        from .predictions import PredictionStore
        store = None if args.no_store else PredictionStore(args.store)
        if args.action == "resolve":
            out = resolve_predictions(reg, store or PredictionStore(args.store), asof=args.asof)
        elif args.action == "accuracy":
            out = forecast_accuracy(store or PredictionStore(args.store), horizon_days=args.horizon)
        elif args.action == "replay":
            report = report_from_store(store or PredictionStore(args.store), run_id=args.run_id)
            out = report if args.report_format == "json" else None
            if out is None:
                print(render_report(report, fmt=args.report_format))
                return 0
        else:  # run
            report = run_daily_report(reg, universe=args.universe, horizon_days=args.horizon,
                                      method=args.method, lookback=args.lookback,
                                      refresh=args.refresh, store=store, asof=args.asof,
                                      check_events=not args.no_events)
            if args.report_format == "json":
                out = report
            else:
                print(render_report(report, fmt=args.report_format))
                return 0
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
