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
from .data import (AlphaVantageProvider, CSVProvider, StooqProvider, SyntheticProvider,
                   YahooProvider)
from .indicators import ema
from .tools import ToolRegistry


def _registry(args) -> ToolRegistry:
    if getattr(args, "yahoo", False):
        return ToolRegistry(YahooProvider())
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
    common.add_argument("--yahoo", action="store_true",
                        help="use live Yahoo Finance data (free, no key; long daily history)")
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

    pdl = sub.add_parser("daily", parents=[common],
                         help="daily forecast report over a universe (default: NASDAQ top 10)")
    pdl.add_argument("--universe", default="nasdaq10", help="named universe (nasdaq10, nasdaq_megacap)")
    pdl.add_argument("--symbols", default=None, help="comma-separated symbols, overrides --universe")
    pdl.add_argument("--horizon", type=int, default=30, help="forecast horizon in calendar days")
    pdl.add_argument("--method", default="drift", choices=["naive", "drift", "blend"])
    pdl.add_argument("--timeframe", default="1d")
    pdl.add_argument("--lookback", type=int, default=400)
    pdl.add_argument("--benchmark", default="QQQ", help="relative-strength benchmark ('' to skip)")
    pdl.add_argument("--refresh-universe", action="store_true",
                     help="re-rank constituents by live market cap (needs a fundamentals feed)")
    pdl.add_argument("--no-skill", action="store_true",
                     help="skip the per-symbol walk-forward skill check (faster)")
    pdl.add_argument("--fundamentals", action="store_true")
    pdl.add_argument("--sentiment", action="store_true")
    pdl.add_argument("--events", action="store_true")
    pdl.add_argument("--db", default="atlas_predictions.db", help="SQLite prediction store")
    pdl.add_argument("--no-store", action="store_true", help="do not persist this run")
    pdl.add_argument("--out", default=None, help="also write the rendered report to this file")
    pdl.add_argument("--render", default=None, choices=["text", "markdown", "html"],
                     help="rendered format (defaults to --format's text, or markdown with --out)")

    pfc = sub.add_parser("forecast", parents=[common], help="horizon price forecast for one symbol")
    pfc.add_argument("symbol")
    pfc.add_argument("--horizon", type=int, default=30)
    pfc.add_argument("--method", default="drift", choices=["naive", "drift", "blend"])
    pfc.add_argument("--timeframe", default="1d")
    pfc.add_argument("--lookback", type=int, default=400)
    pfc.add_argument("--no-skill", action="store_true", help="skip the walk-forward skill check")
    pfc.add_argument("--compare", action="store_true", help="score every method over the same origins")

    ppr = sub.add_parser("predictions", parents=[common],
                         help="query / resolve / score the stored prediction table")
    ppr.add_argument("action", choices=["list", "runs", "report", "resolve", "accuracy", "export", "stats"])
    ppr.add_argument("--db", default="atlas_predictions.db")
    ppr.add_argument("--run-id", type=int, default=None)
    ppr.add_argument("--symbol", default=None)
    ppr.add_argument("--universe", default=None)
    ppr.add_argument("--open", dest="only_open", action="store_true", help="unresolved predictions only")
    ppr.add_argument("--resolved", dest="only_resolved", action="store_true",
                     help="resolved predictions only")
    ppr.add_argument("--asof", default=None, help="resolve as of this date (default: today)")
    ppr.add_argument("--horizon", type=int, default=None)
    ppr.add_argument("--limit", type=int, default=200)
    ppr.add_argument("--lookback", type=int, default=400)
    ppr.add_argument("--render", default="text", choices=["text", "markdown", "html"])
    ppr.add_argument("--out", default=None, help="write the rendered report to this file")

    po = sub.add_parser("option", parents=[common], help="Black-Scholes price + greeks (+ implied vol)")
    po.add_argument("--spot", type=float, required=True)
    po.add_argument("--strike", type=float, required=True)
    po.add_argument("--dte", type=float, required=True, help="days to expiry")
    po.add_argument("--kind", default="call", choices=["call", "put"])
    po.add_argument("--rate", type=float, default=0.04, help="risk-free rate (annual)")
    po.add_argument("--div-yield", type=float, default=0.0)
    po.add_argument("--vol", type=float, default=None, help="volatility (to price)")
    po.add_argument("--price", type=float, default=None, help="market price (to imply vol)")

    pft = sub.add_parser("fetch", parents=[common],
                         help="download full history to CSV cache for offline --csv use")
    pft.add_argument("symbols", help="comma-separated tickers, e.g. AAPL,MSFT,NVDA")
    pft.add_argument("--out", default="./mydata", help="directory to write <SYMBOL>_<TF>.csv into")
    pft.add_argument("--timeframe", default="1d", help="bar timeframe (default 1d)")
    pft.add_argument("--lookback", type=int, default=0,
                     help="bars to keep (0 = full available history, the default)")

    pdg = sub.add_parser("diag", parents=[common],
                         help="diagnose a data feed: recent bars, spacing, largest moves, vol")
    pdg.add_argument("symbol")
    pdg.add_argument("--timeframe", default="1d")
    pdg.add_argument("--lookback", type=int, default=400)

    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .web import serve
        serve(args.host, args.port)
        return 0

    if args.cmd == "fetch":
        import os

        from .data import write_ohlcv_csv
        # Default to Yahoo — free, no key, long daily history, and not behind
        # Stooq's anti-bot wall — unless the user explicitly picked another source.
        if getattr(args, "alpha_vantage", False):
            prov = AlphaVantageProvider(api_key=getattr(args, "api_key", None),
                                        premium=getattr(args, "premium", False))
        elif getattr(args, "stooq", False):
            prov = StooqProvider()
        else:
            prov = YahooProvider()
        os.makedirs(args.out, exist_ok=True)
        written = []
        for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            try:
                series = prov.get_ohlcv(sym, args.timeframe, args.lookback)
                path = os.path.join(args.out, f"{sym.upper()}_{args.timeframe}.csv")
                n = write_ohlcv_csv(series, path)
                written.append({"symbol": sym.upper(), "bars": n,
                                "start": series.ts[0].date().isoformat(),
                                "end": series.ts[-1].date().isoformat(), "path": path})
            except Exception as e:  # noqa: BLE001 - surface per-symbol errors honestly
                written.append({"symbol": sym.upper(), "error": str(e)})
        out = {"source": getattr(prov, "source", "?"), "dir": args.out, "written": written,
               "hint": f"now run: python -m atlas.cli forecast <SYM> --compare --csv {args.out}"}
        print(json.dumps(out, indent=2, default=str))
        return 0 if any("bars" in w for w in written) else 1

    reg = _registry(args)

    if args.cmd == "diag":
        import math as _math
        import statistics as _stats

        fetched = reg.get_ohlcv(args.symbol, args.timeframe, args.lookback)
        if "error" in fetched:
            print(json.dumps(fetched, indent=2, default=str))
            return 1
        s = fetched["_series"]
        closes, ts = list(s.close), list(s.ts)
        gaps = [(ts[i] - ts[i - 1]).days for i in range(1, len(ts))]
        rets = [(_math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 and closes[i] > 0 else None)
                for i in range(1, len(closes))]
        valid = sorted(((abs(r), ts[i + 1].date().isoformat(), round(r * 100, 1))
                        for i, r in enumerate(rets) if r is not None), reverse=True)
        rr = [r for r in rets if r is not None]
        daily_sigma = _stats.pstdev(rr) if len(rr) > 1 else 0.0
        out = {
            "symbol": args.symbol.upper(),
            "provider": getattr(reg.provider, "source", "?"),
            "bars": len(s),
            "asof": s.asof.isoformat() if s.asof else None,
            "median_gap_days": (sorted(gaps)[len(gaps) // 2] if gaps else None),
            "max_gap_days": (max(gaps) if gaps else None),
            "recent": [{"date": ts[i].date().isoformat(), "close": round(closes[i], 2)}
                       for i in range(max(0, len(closes) - 10), len(closes))],
            "largest_daily_moves_pct": [{"date": d, "move_pct": mv} for _, d, mv in valid[:5]],
            "daily_vol_pct": round(daily_sigma * 100, 2),
            "annualized_vol_pct": round(daily_sigma * _math.sqrt(252) * 100, 1),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

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
    elif args.cmd == "daily":
        from .daily import render_daily, run_daily
        from .store import PredictionStore
        symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
        store = None if args.no_store else PredictionStore(args.db)
        try:
            out = run_daily(
                registry=reg, universe=args.universe, symbols=symbols,
                horizon_days=args.horizon, method=args.method, timeframe=args.timeframe,
                lookback=args.lookback, benchmark=(args.benchmark or None),
                with_fundamentals=args.fundamentals, with_sentiment=args.sentiment,
                with_events=args.events, refresh_universe=args.refresh_universe,
                with_skill=not args.no_skill, store=store, persist=not args.no_store,
            )
            fmt = args.render or ("markdown" if args.out else "text")
            if args.out or args.format == "text" or args.render:
                rendered = render_daily(out, fmt)
                if args.out:
                    with open(args.out, "w", encoding="utf-8") as fh:
                        fh.write(rendered)
                    if store is not None:
                        store.record_report(out.get("run_id"), fmt, rendered,
                                            title=f"ATLAS Daily {out['run_date']}", path=args.out)
                    out["written_to"] = args.out
                if args.format == "text" or args.render:
                    print(rendered)
                    if args.out:
                        print(f"\n[written to {args.out}]")
                    return 0
        finally:
            if store is not None:
                store.close()
    elif args.cmd == "forecast":
        if args.compare:
            out = reg.compare_forecast_methods(args.symbol, horizon_days=args.horizon,
                                               timeframe=args.timeframe,
                                               lookback=max(args.lookback, 750))
        else:
            out = reg.forecast_price(args.symbol, horizon_days=args.horizon, method=args.method,
                                     timeframe=args.timeframe, lookback=args.lookback,
                                     with_skill=not args.no_skill)
    elif args.cmd == "predictions":
        from .daily import render_daily, report_from_store
        from .store import PredictionStore
        store = PredictionStore(args.db)
        try:
            if args.action == "runs":
                out = {"runs": store.runs(limit=args.limit)}
            elif args.action == "list":
                resolved = True if args.only_resolved else (False if args.only_open else None)
                rows = store.predictions(run_id=args.run_id, symbol=args.symbol,
                                         resolved=resolved, limit=args.limit)
                out = {"count": len(rows), "rows": rows}
            elif args.action == "accuracy":
                out = {"overall": store.accuracy(symbol=args.symbol, horizon_days=args.horizon),
                       "by_symbol": store.leaderboard()}
            elif args.action == "stats":
                out = store.stats()
            elif args.action == "export":
                csv_text = store.export_csv(run_id=args.run_id, symbol=args.symbol)
                if args.out:
                    with open(args.out, "w", encoding="utf-8") as fh:
                        fh.write(csv_text)
                    out = {"written_to": args.out, "bytes": len(csv_text)}
                else:
                    print(csv_text)
                    return 0
            elif args.action == "resolve":
                out = store.resolve_due(reg, asof=args.asof, lookback=args.lookback)
                out["accuracy"] = store.accuracy()
            else:  # report
                rep = report_from_store(store, run_id=args.run_id, universe=args.universe)
                rendered = render_daily(rep, args.render) if "error" not in rep else rep["error"]
                if args.out:
                    with open(args.out, "w", encoding="utf-8") as fh:
                        fh.write(rendered)
                    store.record_report(rep.get("run_id"), args.render, rendered,
                                        title=f"ATLAS Daily {rep.get('run_date')}", path=args.out)
                    print(f"[written to {args.out}]")
                else:
                    print(rendered)
                return 0
        finally:
            store.close()
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
