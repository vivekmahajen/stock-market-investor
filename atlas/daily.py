"""ATLAS Daily Report — the scheduled NASDAQ-top-10 forecast job.

This module wires the forecast engine, universe resolver, and prediction store
into the single artefact the daily-report system prompt describes: a dated,
per-symbol price-distribution forecast over a fixed universe, with every
prediction written to a store before the market can move, elapsed predictions
resolved, and realised accuracy tracked over time.

The functions here map one-to-one to the Section 3 tools of
``prompts/atlas-daily-report-prompt.md``:

* :func:`run_daily_report`   — the per-symbol loop + persistence.
* :func:`resolve_predictions`— score every prediction whose horizon elapsed.
* :func:`forecast_accuracy`  — realised accuracy over resolved predictions.
* :func:`report_from_store`  — regenerate a report dict from stored records.
* :func:`render_report`      — text / Markdown / self-contained HTML.

Guardrails honoured throughout: no fabricated numbers (every figure comes from a
tool result in the run), forecasts are distributions never targets, simulated and
stale data are labelled loudly at the top, and failed symbols are visible with
their error and excluded from summary statistics.
"""
from __future__ import annotations

import html
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from . import scoring
from .analysis import _risk_subscore, classify_regime
from .forecast import forecast_price
from .predictions import PredictionStore, accuracy_stats, target_date
from .universe import get_universe

MODEL_VERSION = "atlas-daily/1.0 (lognormal drift, walk-forward skill)"
_STALE_LAG_DAYS = 4  # newer than a long weekend => not flagged stale


def _run_date(asof: Optional[str]) -> date:
    if asof:
        return datetime.fromisoformat(str(asof)[:10]).date()
    return datetime.now(timezone.utc).date()


def _series_closes_dated(series) -> List[tuple]:
    return [(series.ts[i].date(), series.close[i]) for i in range(len(series))]


def _symbol_row(registry, symbol: str, run_date: date, horizon_days: int,
                method: str, timeframe: str, lookback: int,
                check_events: bool) -> Dict[str, object]:
    """Fetch, forecast, score and (optionally) event-check one symbol.

    A failed fetch or a refused forecast returns a row carrying ``error`` — never
    a fabricated number and never a silently dropped symbol.
    """
    fetched = registry.get_ohlcv(symbol, timeframe, lookback)
    if "error" in fetched:
        return {"symbol": symbol, "error": fetched["error"]}
    series = fetched["_series"]
    asof_dt = series.asof
    asof_date = asof_dt.date() if asof_dt else None
    last_close = series.close[-1]
    lag_days = (run_date - asof_date).days if asof_date else None

    fc = forecast_price(list(series.close), horizon_days=horizon_days,
                        method=method, with_skill=True)
    if "error" in fc:
        return {"symbol": symbol, "error": fc["error"], "last_close": last_close,
                "asof": asof_date.isoformat() if asof_date else None}

    # ATLAS Score from the sub-scores we can compute here (technical + risk).
    tech = scoring.technical_subscore(series)
    risk = _risk_subscore(series)
    subscores = {"technical": tech, "risk": risk}
    score_value = None
    score_label = None
    try:
        sc = scoring.atlas_score(subscores)
        d = sc.to_dict()
        score_value = d["atlas_score"]
        score_label = d.get("score_label") or sc.label
    except ValueError:
        pass  # too little history to score — left null, not invented

    regime = classify_regime(series)

    skill = fc.get("skill")
    row: Dict[str, object] = {
        "symbol": symbol,
        "asof": asof_date.isoformat() if asof_date else None,
        "lag_days": lag_days,
        "last_close": last_close,
        "median": fc["median"],
        "expected_return": fc["expected_return"],
        "interval_80": fc["interval_80"],
        "interval_95": fc["interval_95"],
        "p_up": fc["p_up"],
        "horizon_days": horizon_days,
        "horizon_trading_days": fc["horizon_trading_days"],
        "target_date": target_date(asof_date or run_date, horizon_days).isoformat(),
        "atlas_score": score_value,
        "score_label": score_label,
        "regime": regime,
        "method": method,
        "skill_score": skill.get("skill_score") if skill else None,
        "beats_random_walk": skill.get("beats_random_walk") if skill else None,
        "skill_folds": skill.get("folds") if skill else None,
        "coverage_80_backtest": skill.get("coverage_80") if skill else None,
        "event_in_horizon": None,
        "simulated": fetched["provenance"].get("simulated", False),
    }

    if check_events:
        row["event_in_horizon"] = _event_flag(registry, symbol, asof_date, horizon_days)
    return row


def _event_flag(registry, symbol: str, asof_date: Optional[date],
                horizon_days: int) -> Optional[dict]:
    """Return the nearest dated event inside the horizon, or a note/None."""
    er = registry.get_earnings_calendar(symbol)
    if "error" in er:
        return {"error": er["error"]}
    if asof_date is None:
        return None
    tgt = target_date(asof_date, horizon_days)
    upcoming = []
    for row in er.get("earnings", []):
        raw = row.get("reportDate") or row.get("date") or row.get("report_date")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(str(raw)[:10]).date()
        except ValueError:
            continue
        if asof_date <= d <= tgt:
            upcoming.append(d.isoformat())
    if not upcoming:
        return None
    return {"type": "earnings", "date": sorted(upcoming)[0], "count": len(upcoming)}


def run_daily_report(registry, universe: str = "nasdaq10", horizon_days: int = 30,
                     method: str = "drift", timeframe: str = "1d", lookback: int = 400,
                     refresh: bool = False, store: Optional[PredictionStore] = None,
                     asof: Optional[str] = None, run_id: Optional[str] = None,
                     check_events: bool = True,
                     resolve_first: bool = True) -> Dict[str, object]:
    """Produce the daily forecast report over ``universe`` and persist predictions.

    Order of operations mirrors the run procedure: resolve the universe, resolve
    yesterday's elapsed predictions first, run the per-symbol loop, log every
    prediction, then attach the realised track record.
    """
    run_date = _run_date(asof)
    run_id = run_id or f"{universe}-{run_date.isoformat()}-{horizon_days}d"
    notes: List[str] = []

    # 1. Resolve the universe (never recalled).
    uni = get_universe(universe, refresh=refresh, provider=registry.provider)
    if uni.get("error"):
        return {"error": uni["error"], "universe": universe}
    notes.extend(uni.get("notes", []))
    constituents = uni["constituents"]

    # 2. Resolve elapsed predictions BEFORE reporting accuracy.
    resolution = None
    if store is not None and resolve_first:
        resolution = resolve_predictions(registry, store, asof=asof)

    # 3. Per-symbol loop.
    rows: List[dict] = []
    ok_rows: List[dict] = []
    for sym in constituents:
        row = _symbol_row(registry, sym, run_date, horizon_days, method,
                          timeframe, lookback, check_events)
        rows.append(row)
        if "error" not in row:
            ok_rows.append(row)
            if store is not None:
                store.log_prediction(
                    run_id=run_id, symbol=sym, asof=row["asof"] or run_date.isoformat(),
                    horizon_days=horizon_days, last_close=row["last_close"],
                    median=row["median"], interval_80=row["interval_80"],
                    interval_95=row["interval_95"], p_up=row["p_up"], method=method,
                    skill_score=row["skill_score"], created=run_date.isoformat(),
                )
        else:
            notes.append(f"{sym} excluded from summary: {row['error']}")

    persisted = False
    if store is not None:
        persisted = store.save()
        if not persisted:
            notes.append("prediction store was unavailable; report was NOT persisted.")

    # Banners: simulated / stale.
    simulated = any(r.get("simulated") for r in ok_rows) or getattr(registry.provider, "simulated", False)
    stale_syms = [r["symbol"] for r in ok_rows if (r.get("lag_days") or 0) > _STALE_LAG_DAYS]
    max_lag = max((r.get("lag_days") or 0) for r in ok_rows) if ok_rows else 0

    # 5. Track record from resolved predictions only.
    accuracy = None
    if store is not None:
        accuracy = forecast_accuracy(store, horizon_days=horizon_days)

    report = {
        "kind": "atlas_daily_report",
        "run_id": run_id,
        "run_date": run_date.isoformat(),
        "universe": universe,
        "universe_description": uni.get("description"),
        "constituents": constituents,
        "ranking_source": uni.get("ranking_source"),
        "universe_as_of": uni.get("as_of"),
        "horizon_days": horizon_days,
        "method": method,
        "model_version": MODEL_VERSION,
        "provider": getattr(registry.provider, "source", "?"),
        "simulated": simulated,
        "stale": bool(stale_syms),
        "stale_symbols": stale_syms,
        "max_lag_days": max_lag,
        "rows": rows,
        "summary": _summary(ok_rows),
        "accuracy": accuracy,
        "resolution": resolution,
        "persisted": persisted if store is not None else None,
        "notes": notes,
        "disclaimer": "Educational analysis, not financial advice. Forecasts are "
                      "probability distributions, not price targets.",
    }
    return report


def _summary(ok_rows: List[dict]) -> Dict[str, object]:
    """Five-line universe summary from the successful rows."""
    if not ok_rows:
        return {"count": 0, "note": "no symbols produced a forecast"}
    rets = sorted(r["expected_return"] for r in ok_rows)
    n = len(rets)
    median_ret = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2
    up = sum(1 for r in ok_rows if r["p_up"] >= 0.5)
    widest = max(ok_rows, key=lambda r: (r["interval_80"][1] - r["interval_80"][0]) / r["last_close"])
    tightest = min(ok_rows, key=lambda r: (r["interval_80"][1] - r["interval_80"][0]) / r["last_close"])
    scored = [r for r in ok_rows if r.get("atlas_score") is not None]
    highest = max(scored, key=lambda r: r["atlas_score"]) if scored else None
    lowest = min(scored, key=lambda r: r["atlas_score"]) if scored else None
    beats = sum(1 for r in ok_rows if r.get("beats_random_walk"))
    return {
        "count": n,
        "median_expected_return": median_ret,
        "p_up_split": {"up": up, "down": n - up},
        "widest_uncertainty": {"symbol": widest["symbol"],
                               "band_pct": (widest["interval_80"][1] - widest["interval_80"][0]) / widest["last_close"]},
        "tightest_uncertainty": {"symbol": tightest["symbol"],
                                 "band_pct": (tightest["interval_80"][1] - tightest["interval_80"][0]) / tightest["last_close"]},
        "highest_score": {"symbol": highest["symbol"], "score": highest["atlas_score"]} if highest else None,
        "lowest_score": {"symbol": lowest["symbol"], "score": lowest["atlas_score"]} if lowest else None,
        "beats_random_walk_count": beats,
    }


def resolve_predictions(registry, store: PredictionStore,
                        asof: Optional[str] = None, lookback: int = 400) -> Dict[str, object]:
    """Resolve every open prediction whose horizon has elapsed in the data."""
    def closes_for(symbol: str):
        fetched = registry.get_ohlcv(symbol, "1d", lookback)
        if "error" in fetched:
            raise RuntimeError(fetched["error"])
        return _series_closes_dated(fetched["_series"])

    result = store.resolve(closes_for, asof=asof)
    if store.available:
        store.save()
    return result


def forecast_accuracy(store: PredictionStore, symbol: Optional[str] = None,
                      horizon_days: Optional[int] = None) -> Dict[str, object]:
    """Realised accuracy over resolved predictions, with the per-symbol leaderboard."""
    return store.accuracy_stats(symbol=symbol, horizon_days=horizon_days)


def report_from_store(store: PredictionStore, run_id: Optional[str] = None) -> Dict[str, object]:
    """Reconstruct a lightweight report dict from stored prediction records."""
    recs = store.records
    if run_id:
        recs = [r for r in recs if r.get("run_id") == run_id]
    else:
        run_ids = [r.get("run_id") for r in recs]
        run_id = run_ids[-1] if run_ids else None
        recs = [r for r in recs if r.get("run_id") == run_id]
    if not recs:
        return {"error": f"no stored predictions for run_id={run_id!r}", "run_id": run_id}
    rows = []
    for r in recs:
        rows.append({
            "symbol": r["symbol"], "asof": r["asof"], "last_close": r["last_close"],
            "median": r["median"], "interval_80": r["interval_80"],
            "interval_95": r["interval_95"], "p_up": r["p_up"],
            "expected_return": (r["median"] / r["last_close"] - 1.0) if r["last_close"] else None,
            "target_date": r["target_date"], "method": r["method"],
            "regime": "—", "atlas_score": None, "score_label": None,
            "skill_score": r.get("skill_score"),
            "beats_random_walk": (r.get("skill_score") or 0) > 0 if r.get("skill_score") is not None else None,
            "event_in_horizon": None,
            "resolved": r.get("resolved"),
            "realized_close": r.get("realized_close"), "outcome": r.get("outcome"),
        })
    # Parse the universe/date out of the run_id (``<universe>-<YYYY-MM-DD>-<N>d``).
    import re
    m = re.match(r"^(?P<u>.+)-(?P<d>\d{4}-\d{2}-\d{2})-\d+d$", run_id or "")
    universe = m.group("u") if m else "replay"
    run_dt = m.group("d") if m else (recs[0].get("created") or "")
    return {
        "kind": "atlas_daily_report_replay",
        "run_id": run_id,
        "run_date": run_dt,
        "universe": universe,
        "universe_description": "reconstructed from prediction store",
        "constituents": [r["symbol"] for r in recs],
        "ranking_source": "from stored run",
        "universe_as_of": None,
        "horizon_days": recs[0]["horizon_days"],
        "method": recs[0].get("method"),
        "model_version": MODEL_VERSION,
        "provider": "stored",
        "simulated": False,
        "stale": False,
        "stale_symbols": [],
        "max_lag_days": 0,
        "rows": rows,
        "summary": _summary([r for r in rows if "error" not in r]),
        "accuracy": accuracy_stats([r for r in recs if r.get("resolved")]),
        "notes": ["Replayed from the prediction store; regime/score not re-derived."],
        "disclaimer": "Educational analysis, not financial advice. Forecasts are "
                      "probability distributions, not price targets.",
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _pct(x: Optional[float], digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%" if isinstance(x, (int, float)) else "—"


def _num(x: Optional[float], digits: int = 2) -> str:
    return f"{x:,.{digits}f}" if isinstance(x, (int, float)) else "—"


def _skill_cell(row: dict) -> str:
    s = row.get("skill_score")
    if s is None:
        return "n/a"
    tag = "beats RW" if row.get("beats_random_walk") else "no edge"
    return f"{s:+.2f} ({tag})"


def render_report(report: Dict[str, object], fmt: str = "text") -> str:
    """Render a report dict as ``text``, ``markdown``, or self-contained ``html``."""
    if "error" in report:
        return f"ATLAS Daily Report error: {report['error']}"
    if fmt == "html":
        return _render_html(report)
    if fmt == "markdown":
        return _render_markdown(report)
    return _render_text(report)


def _banners(report: dict) -> List[str]:
    out = []
    if report.get("simulated"):
        out.append("⚠ SIMULATED DATA — figures below come from a synthetic/demo feed, "
                   "not real market prices. Do not act on them.")
    if report.get("stale"):
        out.append(f"⚠ STALE DATA — newest bar lags the run date by up to "
                   f"{report.get('max_lag_days')} day(s). Forecasts are anchored to the "
                   f"last available close, not today's price. Stale: "
                   f"{', '.join(report.get('stale_symbols', []))}.")
    return out


def _render_text(report: dict) -> str:
    L: List[str] = []
    for b in _banners(report):
        L.append(b)
    if _banners(report):
        L.append("")
    L.append(f"ATLAS DAILY REPORT — {report['universe'].upper()}")
    L.append(f"Run date: {report['run_date']}   Horizon: {report['horizon_days']} calendar days")
    L.append(f"Model: {report['model_version']}   Provider: {report['provider']}")
    L.append(f"Constituent ranking: {report['ranking_source']} "
             f"(as of {report.get('universe_as_of')})")
    L.append("")
    # Table
    hdr = f"{'SYM':<6}{'CLOSE':>10}{'MEDIAN':>10}{'RET':>8}{'80% INTERVAL':>22}{'P(up)':>7}  {'SCORE':<14}{'REGIME':<16}{'SKILL vs RW':<18}EVENT"
    L.append(hdr)
    L.append("-" * len(hdr))
    for r in report["rows"]:
        if "error" in r:
            L.append(f"{r['symbol']:<6}  ERROR: {r['error']}")
            continue
        interval = f"[{_num(r['interval_80'][0])}, {_num(r['interval_80'][1])}]"
        score = f"{r['atlas_score']:.0f} {r.get('score_label') or ''}".strip() if r.get("atlas_score") is not None else "—"
        ev = r.get("event_in_horizon")
        ev_s = ""
        if isinstance(ev, dict):
            ev_s = ev.get("date") or ev.get("error") or ""
        L.append(f"{r['symbol']:<6}{_num(r['last_close']):>10}{_num(r['median']):>10}"
                 f"{_pct(r['expected_return']):>8}{interval:>22}{r['p_up']*100:>6.0f}%  "
                 f"{score:<14}{r['regime']:<16}{_skill_cell(r):<18}{ev_s}")
    L.append("")
    # Summary
    s = report["summary"]
    L.append("SUMMARY")
    if s.get("count"):
        L.append(f"  Median forecast return across {s['count']} names: {_pct(s['median_expected_return'])}")
        L.append(f"  P(up) split: {s['p_up_split']['up']} up / {s['p_up_split']['down']} down")
        L.append(f"  Widest band: {s['widest_uncertainty']['symbol']} "
                 f"({_pct(s['widest_uncertainty']['band_pct'])}); tightest: "
                 f"{s['tightest_uncertainty']['symbol']} ({_pct(s['tightest_uncertainty']['band_pct'])})")
        if s.get("highest_score"):
            L.append(f"  Highest score: {s['highest_score']['symbol']} "
                     f"({s['highest_score']['score']:.0f}); lowest: "
                     f"{s['lowest_score']['symbol']} ({s['lowest_score']['score']:.0f})")
        L.append(f"  Models beating a random walk: {s['beats_random_walk_count']}/{s['count']}")
    else:
        L.append(f"  {s.get('note')}")
    L.append("")
    # Accuracy
    L.append("REALISED ACCURACY")
    L.append(_accuracy_text(report.get("accuracy")))
    L.append("")
    # Notes
    if report.get("notes"):
        L.append("NOTES")
        for n in report["notes"]:
            L.append(f"  - {n}")
        L.append("")
    L.append(report.get("disclaimer", ""))
    return "\n".join(L)


def _accuracy_text(acc: Optional[dict]) -> str:
    if not acc:
        return "  (no prediction store attached)"
    if acc.get("resolved_count", 0) == 0:
        return "  No predictions have resolved yet — the walk-forward skill on each " \
               "row is a backtest, not a realised track record."
    lines = [f"  Resolved predictions: {acc['resolved_count']}"]
    if not acc.get("sufficient"):
        lines.append("  (running tally — under 10 resolved; not yet a hit rate)")
    lines.append(f"  MAPE model {acc['mape_model_pct']:.2f}% vs naive "
                 f"{acc['mape_naive_pct']:.2f}%  (skill {_fmt_skill(acc.get('skill_vs_naive'))})")
    lines.append(f"  80% interval coverage: {acc['coverage_80']*100:.0f}% "
                 f"(nominal 80%); directional accuracy {acc['directional_accuracy']*100:.0f}%")
    lb = acc.get("leaderboard") or []
    if lb:
        top = lb[0]
        lines.append(f"  Best symbol so far: {top['symbol']} "
                     f"(MAPE {top['mape_model_pct']:.2f}%, n={top['resolved_count']})")
    return "\n".join(lines)


def _fmt_skill(s):
    return f"{s:+.2f}" if isinstance(s, (int, float)) else "n/a"


def _render_markdown(report: dict) -> str:
    L: List[str] = []
    for b in _banners(report):
        L.append(f"> {b}\n")
    L.append(f"# ATLAS Daily Report — {report['universe'].upper()}")
    L.append("")
    L.append(f"- **Run date:** {report['run_date']}")
    L.append(f"- **Horizon:** {report['horizon_days']} calendar days")
    L.append(f"- **Model:** {report['model_version']}")
    L.append(f"- **Provider:** {report['provider']}")
    L.append(f"- **Constituent ranking:** {report['ranking_source']} "
             f"(as of {report.get('universe_as_of')})")
    L.append("")
    L.append("| Symbol | Close | Median | Return | 80% Interval | P(up) | Score | Regime | Skill vs RW | Event |")
    L.append("|---|---:|---:|---:|---|---:|---|---|---|---|")
    for r in report["rows"]:
        if "error" in r:
            L.append(f"| {r['symbol']} | ERROR | {html.escape(str(r['error']))} | | | | | | | |")
            continue
        interval = f"{_num(r['interval_80'][0])} – {_num(r['interval_80'][1])}"
        score = f"{r['atlas_score']:.0f} {r.get('score_label') or ''}".strip() if r.get("atlas_score") is not None else "—"
        ev = r.get("event_in_horizon")
        ev_s = (ev.get("date") or ev.get("error") or "") if isinstance(ev, dict) else ""
        L.append(f"| {r['symbol']} | {_num(r['last_close'])} | {_num(r['median'])} | "
                 f"{_pct(r['expected_return'])} | {interval} | {r['p_up']*100:.0f}% | "
                 f"{score} | {r['regime']} | {_skill_cell(r)} | {ev_s} |")
    L.append("")
    s = report["summary"]
    L.append("## Summary")
    if s.get("count"):
        L.append(f"- Median forecast return across {s['count']} names: **{_pct(s['median_expected_return'])}**")
        L.append(f"- P(up) split: {s['p_up_split']['up']} up / {s['p_up_split']['down']} down")
        L.append(f"- Widest band: {s['widest_uncertainty']['symbol']} "
                 f"({_pct(s['widest_uncertainty']['band_pct'])}); tightest "
                 f"{s['tightest_uncertainty']['symbol']} ({_pct(s['tightest_uncertainty']['band_pct'])})")
        if s.get("highest_score"):
            L.append(f"- Highest score {s['highest_score']['symbol']} ({s['highest_score']['score']:.0f}), "
                     f"lowest {s['lowest_score']['symbol']} ({s['lowest_score']['score']:.0f})")
        L.append(f"- Models beating a random walk: **{s['beats_random_walk_count']}/{s['count']}**")
    else:
        L.append(f"- {s.get('note')}")
    L.append("")
    L.append("## Realised accuracy")
    L.append("")
    L.append("```")
    L.append(_accuracy_text(report.get("accuracy")))
    L.append("```")
    if report.get("notes"):
        L.append("")
        L.append("## Notes")
        for n in report["notes"]:
            L.append(f"- {n}")
    L.append("")
    L.append(f"_{report.get('disclaimer', '')}_")
    return "\n".join(L)


def _render_html(report: dict) -> str:
    esc = html.escape
    parts: List[str] = []
    parts.append("<!doctype html><meta charset='utf-8'>")
    parts.append("<title>ATLAS Daily Report</title>")
    parts.append("<style>"
                 "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a;background:#fff}"
                 ".banner{background:#fde68a;border:1px solid #d97706;padding:10px 14px;border-radius:6px;margin:8px 0;font-weight:600}"
                 "table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}"
                 "th,td{border:1px solid #e5e7eb;padding:6px 8px;text-align:right}"
                 "th:first-child,td:first-child{text-align:left}"
                 "thead{background:#f3f4f6}"
                 ".err{color:#b91c1c}"
                 ".foot{color:#6b7280;font-size:12px;margin-top:20px}"
                 "h1{font-size:20px}h2{font-size:16px;margin-top:22px}"
                 "code{white-space:pre-wrap;display:block;background:#f9fafb;padding:10px;border-radius:6px}"
                 "</style>")
    for b in _banners(report):
        parts.append(f"<div class='banner'>{esc(b)}</div>")
    parts.append(f"<h1>ATLAS Daily Report — {esc(report['universe'].upper())}</h1>")
    parts.append("<p>"
                 f"<b>Run date:</b> {esc(report['run_date'])} &nbsp; "
                 f"<b>Horizon:</b> {report['horizon_days']} calendar days &nbsp; "
                 f"<b>Provider:</b> {esc(str(report['provider']))}<br>"
                 f"<b>Model:</b> {esc(report['model_version'])}<br>"
                 f"<b>Constituent ranking:</b> {esc(str(report['ranking_source']))} "
                 f"(as of {esc(str(report.get('universe_as_of')))})</p>")
    parts.append("<table><thead><tr>"
                 "<th>Symbol</th><th>Close</th><th>Median</th><th>Return</th>"
                 "<th>80% Interval</th><th>P(up)</th><th>Score</th><th>Regime</th>"
                 "<th>Skill vs RW</th><th>Event</th></tr></thead><tbody>")
    for r in report["rows"]:
        if "error" in r:
            parts.append(f"<tr><td>{esc(r['symbol'])}</td>"
                         f"<td colspan='9' class='err'>ERROR: {esc(str(r['error']))}</td></tr>")
            continue
        interval = f"{_num(r['interval_80'][0])} – {_num(r['interval_80'][1])}"
        score = f"{r['atlas_score']:.0f} {r.get('score_label') or ''}".strip() if r.get("atlas_score") is not None else "—"
        ev = r.get("event_in_horizon")
        ev_s = (ev.get("date") or ev.get("error") or "") if isinstance(ev, dict) else ""
        parts.append("<tr>"
                     f"<td>{esc(r['symbol'])}</td><td>{_num(r['last_close'])}</td>"
                     f"<td>{_num(r['median'])}</td><td>{_pct(r['expected_return'])}</td>"
                     f"<td>{esc(interval)}</td><td>{r['p_up']*100:.0f}%</td>"
                     f"<td>{esc(score)}</td><td>{esc(r['regime'])}</td>"
                     f"<td>{esc(_skill_cell(r))}</td><td>{esc(str(ev_s))}</td></tr>")
    parts.append("</tbody></table>")
    s = report["summary"]
    parts.append("<h2>Summary</h2><ul>")
    if s.get("count"):
        parts.append(f"<li>Median forecast return across {s['count']} names: <b>{_pct(s['median_expected_return'])}</b></li>")
        parts.append(f"<li>P(up) split: {s['p_up_split']['up']} up / {s['p_up_split']['down']} down</li>")
        parts.append(f"<li>Widest band: {esc(s['widest_uncertainty']['symbol'])} "
                     f"({_pct(s['widest_uncertainty']['band_pct'])}); tightest "
                     f"{esc(s['tightest_uncertainty']['symbol'])} ({_pct(s['tightest_uncertainty']['band_pct'])})</li>")
        if s.get("highest_score"):
            parts.append(f"<li>Highest score {esc(s['highest_score']['symbol'])} ({s['highest_score']['score']:.0f}), "
                         f"lowest {esc(s['lowest_score']['symbol'])} ({s['lowest_score']['score']:.0f})</li>")
        parts.append(f"<li>Models beating a random walk: <b>{s['beats_random_walk_count']}/{s['count']}</b></li>")
    else:
        parts.append(f"<li>{esc(str(s.get('note')))}</li>")
    parts.append("</ul>")
    parts.append("<h2>Realised accuracy</h2>")
    parts.append(f"<code>{esc(_accuracy_text(report.get('accuracy')))}</code>")
    if report.get("notes"):
        parts.append("<h2>Notes</h2><ul>")
        for n in report["notes"]:
            parts.append(f"<li>{esc(str(n))}</li>")
        parts.append("</ul>")
    parts.append(f"<p class='foot'>{esc(report.get('disclaimer', ''))}</p>")
    return "".join(parts)
