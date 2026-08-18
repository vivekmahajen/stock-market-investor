"""The daily report — NASDAQ top 10, a 30-day forecast each, stored and rendered.

One call to :func:`run_daily` does the whole job:

1. Resolve the universe (Section 20) — a dated snapshot, or a live market-cap
   re-ranking, with provenance either way.
2. For each symbol, fetch bars **once** and reuse them for the full analysis
   (regime, ATLAS Score, levels, patterns), the horizon forecast (Section 21),
   and the walk-forward skill check.
3. Persist a ``runs`` row and one ``predictions`` row per symbol
   (Section 22) so the report can be regenerated — and later scored — from the
   table rather than from a re-computation that would give different numbers.
4. Render the report as text, Markdown or a self-contained HTML page.

:func:`report_from_store` rebuilds any past report from the database alone, with
realised outcomes filled in where the horizon has since elapsed.
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from .analysis import analyze, propose_signal
from .forecast import MODEL_VERSION, backtest_forecast, forecast
from .store import PredictionStore
from .tools import ToolRegistry
from .universe import BENCHMARKS, resolve_universe

DISCLAIMER = (
    "Educational analysis, not financial advice. Every price below is the median of a "
    "modelled distribution with an explicit uncertainty band — not a target, not a promise."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_between(earlier: str, later: str) -> Optional[int]:
    try:
        a = datetime.fromisoformat(earlier).date()
        b = datetime.fromisoformat(later).date()
    except (TypeError, ValueError):
        return None
    return (b - a).days


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def run_daily(
    registry: Optional[ToolRegistry] = None,
    universe: str = "nasdaq10",
    symbols: Optional[Sequence[str]] = None,
    horizon_days: int = 30,
    method: str = "drift",
    timeframe: str = "1d",
    lookback: int = 400,
    benchmark: Optional[str] = BENCHMARKS["nasdaq100"],
    with_fundamentals: bool = False,
    with_sentiment: bool = False,
    with_events: bool = False,
    refresh_universe: bool = False,
    with_skill: bool = True,
    store: Optional[PredictionStore] = None,
    db_path: Optional[str] = None,
    persist: bool = True,
    run_date: Optional[str] = None,
) -> dict:
    """Produce (and by default persist) the daily forecast report.

    Returns the report envelope. Symbols whose data fails are reported as rows
    with an ``error`` — the run does not abort, and a failed symbol is never
    silently dropped from the count.
    """
    registry = registry or ToolRegistry()
    run_date = run_date or _today()

    if symbols:
        universe_info = {
            "universe": "custom", "symbols": [s.strip().upper() for s in symbols],
            "count": len(symbols), "ranking_source": "caller-supplied",
            "ranking_asof": None, "market_caps": None,
            "notes": ["Symbols were supplied explicitly; no index membership was verified."],
        }
    else:
        universe_info = resolve_universe(universe, registry=registry, refresh=refresh_universe)

    rows: List[dict] = []
    notes: List[str] = list(universe_info.get("notes") or [])
    simulated_any = False

    for rank, symbol in enumerate(universe_info["symbols"], start=1):
        row = _one_symbol(
            registry, symbol, rank, horizon_days, method, timeframe, lookback,
            benchmark, with_fundamentals, with_sentiment, with_events, with_skill,
        )
        simulated_any = simulated_any or bool(row.get("simulated"))
        rows.append(row)

    ok = [r for r in rows if not r.get("error")]
    failed = [r for r in rows if r.get("error")]
    data_asof = max((r["asof"] for r in ok if r.get("asof")), default=None)
    if data_asof:
        lag = _days_between(data_asof[:10], run_date)
        if lag is not None and lag > 4:
            notes.append(
                f"STALE DATA: the newest bar in this run is {data_asof[:10]}, {lag} days before the "
                f"run date {run_date}. Every forecast is anchored to that close, not to today's price."
            )
    if failed:
        notes.append(
            f"{len(failed)} of {len(rows)} symbols produced no forecast "
            f"({', '.join(r['symbol'] for r in failed)}); they are listed but excluded from the summary."
        )
    if simulated_any:
        notes.append(
            "SIMULATED DATA: at least one symbol came from the synthetic provider. "
            "Nothing in this report describes a real market."
        )

    report = {
        "report": "ATLAS Daily Forecast",
        "run_date": run_date,
        "generated_at": _now_iso(),
        "data_asof": data_asof,
        "universe": universe_info["universe"],
        "ranking_source": universe_info["ranking_source"],
        "ranking_asof": universe_info.get("ranking_asof"),
        "market_caps": universe_info.get("market_caps"),
        "symbols": universe_info["symbols"],
        "horizon_days": horizon_days,
        "target_date": next((r.get("target_date") for r in ok if r.get("target_date")), None),
        "method": method,
        "model_version": MODEL_VERSION,
        "timeframe": timeframe,
        "lookback": lookback,
        "benchmark": benchmark,
        "provider": getattr(registry.provider, "source", type(registry.provider).__name__),
        "data_is_simulated": simulated_any,
        "rows": rows,
        "summary": _summarise(ok, horizon_days),
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }

    if persist:
        owned = store is None
        store = store or PredictionStore(db_path or "atlas_predictions.db")
        try:
            report["run_id"] = _persist(store, report)
            report["accuracy_to_date"] = store.accuracy(horizon_days=horizon_days)
        finally:
            if owned:
                store.close()
    return report


def _one_symbol(registry, symbol, rank, horizon_days, method, timeframe, lookback,
                benchmark, with_fundamentals, with_sentiment, with_events, with_skill) -> dict:
    """Fetch once, then analyse + forecast + skill-check off the same bars."""
    fetched = registry.get_ohlcv(symbol, timeframe, lookback)
    if "error" in fetched:
        return {"symbol": symbol, "rank": rank, "error": fetched["error"]}
    series = fetched["_series"]
    prov = fetched.get("provenance", {})
    simulated = bool(prov.get("simulated"))

    fc = forecast(series, horizon_days=horizon_days, method=method)
    if "error" in fc:
        return {"symbol": symbol, "rank": rank, "error": fc["error"], "simulated": simulated}

    a = analyze(symbol, registry=registry, timeframe=timeframe, lookback=lookback,
                benchmark=benchmark, with_fundamentals=with_fundamentals,
                with_sentiment=with_sentiment, with_events=with_events,
                series=series, provenance=prov)
    subs = a.get("subscores") or {}

    sig = propose_signal(symbol, registry=registry, series=series)
    skill = backtest_forecast(series, horizon_days=horizon_days, method=method) if with_skill else None

    event_risk = None
    events = a.get("events") or []
    if events and isinstance(events[0], dict) and "date" in events[0]:
        e = events[0]
        event_risk = f"{e.get('type', 'event')} {e.get('date')} ({e.get('days_away')}d, {e.get('risk')})"

    warnings = list(fc.get("warnings") or [])
    if skill and skill.get("warnings"):
        warnings.extend(skill["warnings"])

    return {
        "symbol": symbol,
        "rank": rank,
        "asof": fc["asof"],
        "target_date": fc["target_date"],
        "horizon_days": horizon_days,
        "last_close": fc["last_close"],
        "forecast_price": fc["forecast_price"],
        "expected_price": fc["expected_price"],
        "forecast_return_pct": fc["forecast_return_pct"],
        "lo80": fc["interval_80"]["low"], "hi80": fc["interval_80"]["high"],
        "lo95": fc["interval_95"]["low"], "hi95": fc["interval_95"]["high"],
        "interval_80_width_pct": fc["interval_80_width_pct"],
        "prob_up": fc["prob_up"],
        "sigma_annual_pct": fc["components"]["sigma_annual_pct"],
        "sigma_horizon": fc["components"]["sigma_horizon"],
        "mu_horizon": fc["components"]["mu_horizon"],
        "method": method,
        "model_version": fc["model_version"],
        "atlas_score": a.get("atlas_score"),
        "score_label": a.get("score_label"),
        "regime": a.get("regime"),
        "confluence": (a.get("confluence") or {}).get("score"),
        "technical": subs.get("technical"),
        "fundamental": subs.get("fundamental"),
        "sentiment": subs.get("sentiment"),
        "relative_strength": subs.get("relative_strength"),
        "risk": subs.get("risk"),
        "signal_direction": sig.get("direction"),
        "signal_confidence": sig.get("confidence"),
        "skill_vs_naive": (skill or {}).get("skill_vs_naive"),
        "backtest_mape_pct": (skill or {}).get("mape_pct"),
        "backtest_samples": (skill or {}).get("samples"),
        "directional_accuracy_pct": (skill or {}).get("directional_accuracy_pct"),
        "coverage_80_pct": (skill or {}).get("coverage_80_pct"),
        "skill_verdict": (skill or {}).get("verdict"),
        "event_risk": event_risk,
        "top_contributors": a.get("top_contributors"),
        "simulated": simulated,
        "warnings": warnings,
    }


def _summarise(rows: List[dict], horizon_days: int) -> dict:
    if not rows:
        return {"count": 0, "note": "No symbol produced a forecast."}
    rets = [r["forecast_return_pct"] for r in rows if r.get("forecast_return_pct") is not None]
    ups = [r for r in rows if (r.get("prob_up") or 0) > 0.5]
    banded = [r for r in rows if _band_width(r) is not None]
    widest = max(banded, key=_band_width) if banded else None
    tightest = min(banded, key=_band_width) if banded else None
    scored = [r for r in rows if r.get("atlas_score") is not None]
    best = max(scored, key=lambda r: r["atlas_score"]) if scored else None
    worst = min(scored, key=lambda r: r["atlas_score"]) if scored else None
    measured = [r for r in rows if r.get("skill_vs_naive") is not None]
    skilled = [r for r in measured if r["skill_vs_naive"] > 0]
    regimes: Dict[str, int] = {}
    for r in rows:
        regimes[r.get("regime") or "unknown"] = regimes.get(r.get("regime") or "unknown", 0) + 1

    return {
        "count": len(rows),
        "median_forecast_return_pct": round(median(rets), 3) if rets else None,
        "forecast_up": len(ups),
        "forecast_down": len(rows) - len(ups),
        "regime_mix": regimes,
        "highest_score": {"symbol": best["symbol"], "atlas_score": best["atlas_score"],
                          "label": best["score_label"]} if best else None,
        "lowest_score": {"symbol": worst["symbol"], "atlas_score": worst["atlas_score"],
                         "label": worst["score_label"]} if worst else None,
        "widest_uncertainty": ({"symbol": widest["symbol"],
                                "interval_80_width_pct": round(_band_width(widest), 2)}
                               if widest else None),
        "tightest_uncertainty": ({"symbol": tightest["symbol"],
                                  "interval_80_width_pct": round(_band_width(tightest), 2)}
                                 if tightest else None),
        "symbols_with_positive_skill": [r["symbol"] for r in skilled],
        "skill_measured": len(measured),
        "skill_note": (
            f"{len(skilled)} of {len(measured)} symbols with a walk-forward check show a "
            f"{horizon_days}-day model that beats a random walk. For the rest, the interval is the "
            f"output — the point forecast carries no demonstrated edge."
            if measured else
            "Walk-forward skill was not measured on this run, so no point forecast here has a "
            "demonstrated edge over assuming the price does not move. Read the intervals, not the points."
        ),
    }


def _band_width(row: dict) -> Optional[float]:
    """80% band width as a percentage of the last close, stored or derived."""
    if row.get("interval_80_width_pct") is not None:
        return float(row["interval_80_width_pct"])
    lo, hi, last = row.get("lo80"), row.get("hi80"), row.get("last_close")
    if None in (lo, hi, last) or not last:
        return None
    return (hi - lo) / last * 100


def _persist(store: PredictionStore, report: dict) -> int:
    run_id = store.record_run(
        universe=report["universe"], horizon_days=report["horizon_days"],
        method=report["method"], model_version=report["model_version"],
        provider=report.get("provider"), timeframe=report["timeframe"],
        lookback=report.get("lookback"), ranking_source=report.get("ranking_source"),
        symbol_count=len([r for r in report["rows"] if not r.get("error")]),
        simulated=report.get("data_is_simulated", False),
        run_date=report["run_date"], notes=report.get("notes"),
    )
    for row in report["rows"]:
        if row.get("error"):
            continue
        store.record_prediction(run_id, row)
    return run_id


# --------------------------------------------------------------------------- #
# Regenerate a report from the stored table
# --------------------------------------------------------------------------- #
def report_from_store(store: PredictionStore, run_id: Optional[int] = None,
                      universe: Optional[str] = None) -> dict:
    """Rebuild a report envelope from stored rows, including realised outcomes.

    This is the point of the table: the same run renders the same report months
    later, now annotated with what actually happened.
    """
    run = store.run(run_id) if run_id is not None else store.latest_run(universe)
    if run is None:
        return {"error": "no stored runs yet — run the daily report at least once."}
    rows = store.predictions(run_id=run["id"], limit=1000)
    ok = [r for r in rows if r.get("forecast_return_pct") is not None]
    resolved = [r for r in rows if r.get("actual_price") is not None]
    return {
        "report": "ATLAS Daily Forecast (from store)",
        "run_id": run["id"],
        "run_date": run["run_date"],
        "generated_at": run["created_at"],
        "regenerated_at": _now_iso(),
        "universe": run["universe"],
        "ranking_source": run.get("ranking_source"),
        "provider": run.get("provider"),
        "timeframe": run.get("timeframe"),
        "lookback": run.get("lookback"),
        "horizon_days": run["horizon_days"],
        "target_date": next((r.get("target_date") for r in rows if r.get("target_date")), None),
        "method": run["method"],
        "model_version": run["model_version"],
        "data_is_simulated": run["simulated"],
        "symbols": [r["symbol"] for r in rows],
        "rows": rows,
        "summary": _summarise(ok, run["horizon_days"]),
        "resolved_count": len(resolved),
        "accuracy_to_date": store.accuracy(horizon_days=run["horizon_days"]),
        "notes": run.get("notes") or [],
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt(v, places: int = 2, dash: str = "–") -> str:
    if v is None:
        return dash
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,.{places}f}"
    return str(v)


def _pct(v, places: int = 2) -> str:
    return "–" if v is None else f"{v:+.{places}f}%"


def render_daily_text(rep: dict, width: int = 100) -> str:
    """Terminal-friendly report."""
    if rep.get("error"):
        return f"DAILY REPORT ERROR: {rep['error']}"
    L: List[str] = []
    sim = "  [SIMULATED DATA]" if rep.get("data_is_simulated") else ""
    L.append("=" * width)
    L.append(f"  ATLAS DAILY FORECAST — {rep['universe']} — {rep['run_date']}{sim}")
    L.append(f"  horizon {rep['horizon_days']}d (target {rep.get('target_date') or '?'}) · "
             f"model {rep['method']}/{rep['model_version']} · provider {rep.get('provider')}")
    L.append(f"  constituents: {rep.get('ranking_source')}"
             + (f" as of {rep['ranking_asof']}" if rep.get("ranking_asof") else ""))
    L.append("=" * width)
    L.append("")
    hdr = (f"  {'#':<3}{'SYM':<7}{'LAST':>10}{'FCAST':>10}{'RET%':>8}"
           f"{'80% LOW':>10}{'80% HIGH':>10}{'P(up)':>8}{'ATLAS':>7}  {'REGIME':<14}SKILL")
    L.append(hdr)
    L.append("  " + "-" * (width - 4))
    for r in rep["rows"]:
        if r.get("error"):
            L.append(f"  {r.get('rank', '?'):<3}{r['symbol']:<7}  ERROR: {r['error'][:60]}")
            continue
        skill = r.get("skill_vs_naive")
        skill_s = "–" if skill is None else f"{skill * 100:+.1f}%"
        prob_s = "–" if r.get("prob_up") is None else f"{r['prob_up']:.0%}"
        L.append(
            f"  {str(r.get('rank', '')):<3}{r['symbol']:<7}{_fmt(r['last_close']):>10}"
            f"{_fmt(r['forecast_price']):>10}{_pct(r['forecast_return_pct'], 1):>8}"
            f"{_fmt(r['lo80']):>10}{_fmt(r['hi80']):>10}{prob_s:>8}"
            f"{_fmt(r.get('atlas_score'), 0):>7}  {str(r.get('regime') or '–'):<14}{skill_s}"
        )
    s = rep.get("summary") or {}
    L.append("")
    L.append("  SUMMARY")
    L.append(f"    median {rep['horizon_days']}d forecast return: {_pct(s.get('median_forecast_return_pct'))}"
             f"   ({s.get('forecast_up', 0)} up / {s.get('forecast_down', 0)} down by P(up))")
    if s.get("highest_score"):
        h = s["highest_score"]
        L.append(f"    highest ATLAS Score: {h['symbol']} {h['atlas_score']} ({h['label']})")
    if s.get("widest_uncertainty"):
        w = s["widest_uncertainty"]
        L.append(f"    widest 80% band: {w['symbol']} at {_fmt(w['interval_80_width_pct'])}% of price")
    if s.get("skill_note"):
        L.append(f"    {s['skill_note']}")
    acc = rep.get("accuracy_to_date") or {}
    if acc:
        L.append("")
        L.append("  REALISED ACCURACY (resolved predictions in the store)")
        if acc.get("resolved"):
            L.append(f"    n={acc['resolved']}  MAPE {_fmt(acc.get('mape_pct'))}%  "
                     f"vs naive {_fmt(acc.get('naive_mape_pct'))}%  "
                     f"80% coverage {_fmt(acc.get('coverage_80_pct'))}%  "
                     f"direction {_fmt(acc.get('directional_accuracy_pct'))}%")
        L.append(f"    {acc.get('note', '')}")
    if rep.get("notes"):
        L.append("")
        L.append("  NOTES")
        for n in rep["notes"]:
            L.append(f"    - {n}")
    L.append("")
    L.append("  " + rep.get("disclaimer", DISCLAIMER))
    return "\n".join(L)


def render_daily_markdown(rep: dict) -> str:
    if rep.get("error"):
        return f"**Daily report error:** {rep['error']}"
    L: List[str] = []
    L.append(f"# ATLAS Daily Forecast — {rep['universe']} — {rep['run_date']}")
    if rep.get("data_is_simulated"):
        L.append("\n> **SIMULATED DATA** — this run used the synthetic provider. "
                 "Nothing here describes a real market.\n")
    L.append(f"\n*Horizon {rep['horizon_days']} calendar days (target **{rep.get('target_date') or '?'}**) · "
             f"model `{rep['method']}` / `{rep['model_version']}` · provider `{rep.get('provider')}` · "
             f"constituents from {rep.get('ranking_source')}"
             + (f" as of {rep['ranking_asof']}" if rep.get("ranking_asof") else "") + "*\n")
    L.append("| # | Symbol | Last | Forecast | Return | 80% band | P(up) | ATLAS | Regime | Skill vs naive |")
    L.append("|---|--------|-----:|---------:|-------:|:---------|------:|------:|--------|---------------:|")
    for r in rep["rows"]:
        if r.get("error"):
            L.append(f"| {r.get('rank','')} | **{r['symbol']}** | — | — | — | — | — | — | error | {r['error'][:60]} |")
            continue
        band = f"{_fmt(r['lo80'])} – {_fmt(r['hi80'])}"
        pu = "–" if r.get("prob_up") is None else f"{r['prob_up']:.0%}"
        sk = "–" if r.get("skill_vs_naive") is None else f"{r['skill_vs_naive']*100:+.1f}%"
        L.append(f"| {r.get('rank','')} | **{r['symbol']}** | {_fmt(r['last_close'])} | "
                 f"{_fmt(r['forecast_price'])} | {_pct(r['forecast_return_pct'],1)} | {band} | {pu} | "
                 f"{_fmt(r.get('atlas_score'),0)} | {r.get('regime') or '–'} | {sk} |")
    s = rep.get("summary") or {}
    L.append("\n## Summary\n")
    L.append(f"- Median {rep['horizon_days']}-day forecast return: **{_pct(s.get('median_forecast_return_pct'))}** "
             f"({s.get('forecast_up',0)} up / {s.get('forecast_down',0)} down by P(up))")
    if s.get("highest_score"):
        h = s["highest_score"]
        L.append(f"- Highest ATLAS Score: **{h['symbol']}** {h['atlas_score']} ({h['label']})")
    if s.get("widest_uncertainty"):
        w = s["widest_uncertainty"]
        L.append(f"- Widest 80% band: **{w['symbol']}** at {_fmt(w['interval_80_width_pct'])}% of price")
    if s.get("skill_note"):
        L.append(f"- {s['skill_note']}")
    acc = rep.get("accuracy_to_date") or {}
    if acc:
        L.append("\n## Realised accuracy to date\n")
        if acc.get("resolved"):
            L.append(f"- Resolved predictions: **{acc['resolved']}** (open: {acc.get('open')})")
            L.append(f"- MAPE **{_fmt(acc.get('mape_pct'))}%** vs naive {_fmt(acc.get('naive_mape_pct'))}% "
                     f"→ skill {_fmt((acc.get('skill_vs_naive') or 0)*100)}%")
            L.append(f"- 80% band coverage {_fmt(acc.get('coverage_80_pct'))}% · "
                     f"directional accuracy {_fmt(acc.get('directional_accuracy_pct'))}%")
        L.append(f"- {acc.get('note','')}")
    if rep.get("notes"):
        L.append("\n## Notes\n")
        for n in rep["notes"]:
            L.append(f"- {n}")
    L.append(f"\n---\n\n*{rep.get('disclaimer', DISCLAIMER)}*")
    return "\n".join(L)


_HTML_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--line:#30363d;--text:#e6edf3;
      --muted:#8b949e;--accent:#4ea1ff;--green:#3fb950;--red:#f85149;--amber:#d29922;--teal:#2dd4bf}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:var(--bg);color:var(--text);
     font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5}
h1{font-size:22px;margin:0 0 4px} h2{font-size:13px;text-transform:uppercase;letter-spacing:.6px;
   color:var(--muted);margin:26px 0 10px}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.sim{display:inline-block;background:var(--amber);color:#04101f;font-weight:700;
     padding:3px 10px;border-radius:6px;font-size:12px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
tr:hover{background:var(--panel2)}
.up{color:var(--green)} .down{color:var(--red)} .muted{color:var(--muted)}
.band{position:relative;height:20px;min-width:150px;background:var(--panel);border:1px solid var(--line);border-radius:4px}
.band .rng{position:absolute;top:4px;height:10px;background:rgba(78,161,255,.28);border-radius:3px}
.band .now{position:absolute;top:1px;width:2px;height:17px;background:var(--muted)}
.band .fc{position:absolute;top:0;width:2px;height:19px;background:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.card .v{font-size:22px;font-weight:700;margin-top:3px}
ul{padding-left:18px;color:var(--muted);font-size:13px}
.foot{margin-top:26px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
"""


def render_daily_html(rep: dict) -> str:
    """A self-contained dark-themed HTML report (no external assets)."""
    if rep.get("error"):
        return f"<!doctype html><meta charset='utf-8'><body>Daily report error: {_html.escape(rep['error'])}</body>"
    e = _html.escape
    rows_html: List[str] = []
    for r in rep["rows"]:
        if r.get("error"):
            rows_html.append(
                f"<tr><td>{e(str(r.get('rank','')))}</td><td><b>{e(r['symbol'])}</b></td>"
                f"<td colspan='8' class='muted'>error: {e(r['error'][:120])}</td></tr>")
            continue
        ret = r.get("forecast_return_pct")
        cls = "up" if (ret or 0) > 0 else ("down" if (ret or 0) < 0 else "muted")
        lo, hi, last, fc = r.get("lo95"), r.get("hi95"), r.get("last_close"), r.get("forecast_price")
        band = ""
        if None not in (lo, hi, last, fc) and hi > lo:
            span = hi - lo
            l80 = (r["lo80"] - lo) / span * 100
            w80 = (r["hi80"] - r["lo80"]) / span * 100
            band = (f"<div class='band' title='95% {_fmt(lo)}–{_fmt(hi)}'>"
                    f"<div class='rng' style='left:{l80:.1f}%;width:{w80:.1f}%'></div>"
                    f"<div class='now' style='left:{(last-lo)/span*100:.1f}%'></div>"
                    f"<div class='fc' style='left:{(fc-lo)/span*100:.1f}%'></div></div>")
        pu = "–" if r.get("prob_up") is None else f"{r['prob_up']:.0%}"
        sk = "–" if r.get("skill_vs_naive") is None else f"{r['skill_vs_naive']*100:+.1f}%"
        actual = ("" if r.get("actual_price") is None
                  else f"<td>{_fmt(r['actual_price'])}</td><td>{_pct(r.get('signed_error_pct'))}</td>")
        rows_html.append(
            f"<tr><td>{e(str(r.get('rank','')))}</td><td><b>{e(r['symbol'])}</b></td>"
            f"<td>{_fmt(r['last_close'])}</td><td>{_fmt(r['forecast_price'])}</td>"
            f"<td class='{cls}'>{_pct(ret,1)}</td>"
            f"<td class='muted'>{_fmt(r['lo80'])} – {_fmt(r['hi80'])}</td>"
            f"<td>{band}</td><td>{pu}</td><td>{_fmt(r.get('atlas_score'),0)}</td>"
            f"<td class='muted'>{e(str(r.get('regime') or '–'))}</td><td>{sk}</td>{actual}</tr>")

    s = rep.get("summary") or {}
    cards = [
        ("Symbols", str(s.get("count", 0))),
        (f"Median {rep['horizon_days']}d return", _pct(s.get("median_forecast_return_pct"))),
        ("Up / down by P(up)", f"{s.get('forecast_up',0)} / {s.get('forecast_down',0)}"),
        ("Beat random walk", f"{len(s.get('symbols_with_positive_skill') or [])} / {s.get('count',0)}"),
    ]
    acc = rep.get("accuracy_to_date") or {}
    if acc.get("resolved"):
        cards.append(("Resolved to date", str(acc["resolved"])))
        cards.append(("Realised MAPE", f"{_fmt(acc.get('mape_pct'))}%"))

    notes = "".join(f"<li>{e(n)}</li>" for n in (rep.get("notes") or []))
    extra_head = ("<th>Actual</th><th>Err</th>"
                  if any(r.get("actual_price") is not None for r in rep["rows"]) else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLAS Daily Forecast — {e(rep['run_date'])}</title>
<style>{_HTML_CSS}</style></head><body>
{"<div class='sim'>SIMULATED DATA — not a real market</div>" if rep.get("data_is_simulated") else ""}
<h1>ATLAS Daily Forecast — {e(str(rep['universe']))}</h1>
<div class="sub">{e(rep['run_date'])} · horizon {rep['horizon_days']} calendar days
 (target {e(str(rep.get('target_date') or '?'))}) · model {e(rep['method'])}/{e(rep['model_version'])}
 · provider {e(str(rep.get('provider')))} · constituents {e(str(rep.get('ranking_source')))}</div>
<div class="cards">{''.join(f"<div class='card'><div class='l'>{e(l)}</div><div class='v'>{e(v)}</div></div>" for l, v in cards)}</div>
<h2>Forecasts</h2>
<table><thead><tr><th>#</th><th>Symbol</th><th>Last</th><th>Forecast</th><th>Return</th>
<th>80% band</th><th>95% range</th><th>P(up)</th><th>ATLAS</th><th>Regime</th><th>Skill</th>{extra_head}</tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>
<h2>Summary</h2>
<ul><li>{e(str(s.get('skill_note','')))}</li>
{f"<li>Highest ATLAS Score: <b>{e(s['highest_score']['symbol'])}</b> {s['highest_score']['atlas_score']} ({e(str(s['highest_score']['label']))})</li>" if s.get('highest_score') else ''}
{f"<li>Widest 80% band: <b>{e(s['widest_uncertainty']['symbol'])}</b> at {_fmt(s['widest_uncertainty']['interval_80_width_pct'])}% of price</li>" if s.get('widest_uncertainty') else ''}
{f"<li>Realised accuracy: {e(str(acc.get('note','')))}</li>" if acc else ''}
</ul>
{f"<h2>Notes</h2><ul>{notes}</ul>" if notes else ""}
<div class="foot">{e(rep.get('disclaimer', DISCLAIMER))}<br>
Generated {e(str(rep.get('generated_at','')))}. The blue marker is the median forecast, the grey
marker today's close, the shaded span the 80% interval inside the 95% range.</div>
</body></html>"""


RENDERERS = {"text": render_daily_text, "markdown": render_daily_markdown,
             "md": render_daily_markdown, "html": render_daily_html}


def render_daily(rep: dict, fmt: str = "text") -> str:
    """Render a report envelope in ``text``, ``markdown`` or ``html``."""
    fn = RENDERERS.get(fmt)
    if fn is None:
        return json.dumps(rep, indent=2, default=str)
    return fn(rep)
