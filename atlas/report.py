"""Human-readable text rendering of ATLAS outputs (Section 15 layered answer).

Renders the structured JSON envelopes into a concise, terminal-friendly report:
headline, sub-scores, plan/levels, evidence, the other side, and the reminder.
The JSON stays the source of truth; this is the prose view alongside it.
"""
from __future__ import annotations

from typing import List, Optional


def _bar(value: Optional[float], width: int = 20) -> str:
    if value is None:
        return "  n/a"
    filled = int(round(value / 100.0 * width))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {value:5.1f}"


def _hr(char: str = "-", n: int = 60) -> str:
    return char * n


def format_analysis(out: dict) -> str:
    if "error" in out:
        return f"ERROR analysing {out.get('symbol', '?')}: {out['error']}"

    L: List[str] = []
    sym = out["symbol"]
    label = out.get("score_label", "?").upper()
    score = out.get("atlas_score")
    regime = out.get("regime", "?")
    sim = " [SIMULATED DATA]" if out.get("data_is_simulated") else ""
    L.append(_hr("="))
    L.append(f"  {sym}  —  ATLAS {score}  ({label})   regime: {regime}{sim}")
    L.append(f"  as of {out.get('asof', '?')}   horizon: {out.get('score_horizon', '?')}")
    L.append(_hr("="))

    # Sub-scores
    subs = out.get("subscores", {})
    L.append("SUB-SCORES")
    for k in ("technical", "fundamental", "sentiment", "relative_strength", "risk"):
        L.append(f"  {k:<18} {_bar(subs.get(k))}")
    conf = out.get("confluence", {})
    if conf.get("score") is not None:
        L.append(f"  {'confluence (TA)':<18} {_bar(conf['score'])}")

    if out.get("top_contributors"):
        L.append("")
        L.append("TOP CONTRIBUTORS: " + "; ".join(out["top_contributors"]))

    # Levels
    lv = out.get("levels", {})
    L.append("")
    L.append(f"LEVELS   last close: {lv.get('last_close')}")
    ns, nr = lv.get("nearest_support"), lv.get("nearest_resistance")
    if nr:
        L.append(f"  nearest resistance: {nr.get('price')}  (touches {nr.get('touches')})")
    if ns:
        L.append(f"  nearest support:    {ns.get('price')}  (touches {ns.get('touches')})")
    if lv.get("resistance"):
        L.append("  resistance above: " + ", ".join(str(x) for x in lv["resistance"][:5]))
    if lv.get("support"):
        L.append("  support below:    " + ", ".join(str(x) for x in lv["support"][:5]))

    # Fundamentals / sentiment detail
    fd = out.get("fundamentals_detail")
    if fd:
        L.append("")
        L.append(f"FUNDAMENTALS  {fd.get('name', sym)} [{fd.get('sector', '?')}]  score {fd.get('score')}")
        L.append("  " + "; ".join(fd.get("contributors", [])))
    sd = out.get("sentiment_detail")
    if sd:
        L.append("")
        L.append(f"SENTIMENT  score {sd.get('score')}  ({sd.get('articles')} articles, avg {sd.get('avg_sentiment')})")
        if sd.get("label_mix"):
            L.append("  " + ", ".join(f"{k}:{v}" for k, v in sd["label_mix"].items()))

    # Patterns
    pats = out.get("patterns", {})
    found = []
    for fam in ("candlestick", "classical", "harmonic"):
        for p in pats.get(fam, []) or []:
            found.append(f"{p.get('name')} ({p.get('direction', '?')})")
    if found:
        L.append("")
        L.append("PATTERNS: " + ", ".join(found))

    # Events
    events = out.get("events") or []
    if events:
        L.append("")
        L.append("EVENT RISK:")
        for e in events:
            L.append(f"  {e.get('type')} on {e.get('date')} — {e.get('days_away')}d away — {e.get('risk','?').upper()}")

    # Notes (the "other side" / caveats)
    if out.get("notes"):
        L.append("")
        L.append("NOTES:")
        for n in out["notes"]:
            L.append(f"  - {n}")

    L.append("")
    L.append(out.get("disclaimer", "Educational analysis, not financial advice."))
    return "\n".join(L)


def format_signal(out: dict) -> str:
    if out.get("error"):
        return f"SIGNAL ERROR for {out.get('symbol','?')}: {out['error']}"
    L: List[str] = []
    if out.get("direction") == "flat":
        L.append(_hr("="))
        L.append(f"  {out['symbol']}  —  NO SETUP (flat)")
        L.append(_hr("="))
        L.append(f"  {out.get('reason','')}")
        L.append("")
        L.append(out.get("disclaimer", ""))
        return "\n".join(L)
    L.append(_hr("="))
    conf = out.get("confidence")
    L.append(f"  SIGNAL  {out['symbol']}  {out['direction'].upper()}"
             + (f"   confidence {conf}" if conf is not None else ""))
    L.append(_hr("="))
    if out.get("thesis"):
        L.append(f"  {out['thesis']}")
    L.append(f"  entry {out['entry']}   stop {out['stop']}   targets {out.get('targets')}")
    L.append(f"  R:R (T1) {out.get('r_multiple')}   horizon: {out.get('horizon')}")
    ps = out.get("position_size", {})
    L.append(f"  size: {ps.get('units')} units   risk {ps.get('risk_pct')}   "
             f"worst-case loss {ps.get('worst_case_loss')}")
    if out.get("confidence_drivers"):
        L.append(f"  drivers: {', '.join(out['confidence_drivers'])}")
    if out.get("biggest_risk"):
        L.append(f"  biggest risk: {out['biggest_risk']}")
    if out.get("what_would_make_me_wrong"):
        L.append(f"  invalidation: {out['what_would_make_me_wrong']}")
    if out.get("catalyst_or_expiry"):
        L.append(f"  catalyst/expiry: {out['catalyst_or_expiry']}")
    if out.get("events"):
        L.append("  events:")
        for e in out["events"]:
            if "error" in e:
                L.append(f"    (calendar unavailable: {e['error']})")
            else:
                L.append(f"    {e.get('type')} {e.get('date')} — {e.get('risk','?').upper()}")
    if out.get("warnings"):
        L.append("  WARNINGS:")
        for w in out["warnings"]:
            L.append(f"    ! {w}")
    L.append("")
    L.append(out.get("disclaimer", ""))
    return "\n".join(L)


def format_backtest(out: dict) -> str:
    m = out.get("metrics", {})
    L: List[str] = []
    L.append(_hr("="))
    L.append(f"  BACKTEST   verdict: {out.get('verdict', '?')}")
    L.append(_hr("="))
    order = [
        ("total_return_pct", "total return %"), ("cagr_pct", "CAGR %"),
        ("max_drawdown_pct", "max drawdown %"), ("sharpe", "Sharpe"),
        ("sortino", "Sortino"), ("calmar", "Calmar"),
        ("win_rate_pct", "win rate %"), ("profit_factor", "profit factor"),
        ("expectancy_per_trade", "expectancy/trade"), ("num_trades", "trades"),
        ("exposure_pct", "exposure %"),
    ]
    for key, lbl in order:
        if key in m:
            L.append(f"  {lbl:<18} {m[key]}")
    if out.get("warnings"):
        for w in out["warnings"]:
            L.append(f"  ! {w}")
    return "\n".join(L)


def format_option(out: dict) -> str:
    if "error" in out:
        return f"OPTION ERROR: {out['error']}"
    g = out.get("greeks", {})
    L: List[str] = []
    L.append(_hr("="))
    L.append(f"  {out['kind'].upper()}  spot {out['spot']}  strike {out['strike']}  ({out['moneyness']})")
    L.append(_hr("="))
    L.append(f"  price: {out['price']}   sigma: {out['sigma']}"
             + (f"   (implied)" if out.get('implied_vol') is not None else ""))
    L.append(f"  T: {out['T_years']}y   rate: {out['rate']}   div yield: {out['dividend_yield']}")
    L.append("  greeks:")
    for k in ("delta", "gamma", "theta", "vega", "rho"):
        if k in g:
            L.append(f"    {k:<7} {g[k]}")
    return "\n".join(L)


def format_score(out: dict) -> str:
    if out.get("error"):
        return f"SCORE ERROR for {out.get('symbol','?')}: {out['error']}"
    subs = out.get("subscores", {})
    L = [_hr("=")]
    L.append(f"  {out['symbol']}  ATLAS {out.get('atlas_score')}  ({str(out.get('score_label','')).upper()})"
             f"   horizon: {out.get('score_horizon','')}")
    L.append(_hr("="))
    for k in ("technical", "fundamental", "sentiment", "relative_strength", "risk"):
        L.append(f"  {k:<18} {_bar(subs.get(k))}")
    if out.get("top_contributors"):
        L.append("  " + "; ".join(out["top_contributors"]))
    return "\n".join(L)


def format_watch(out: dict) -> str:
    rows = out.get("results", [])
    L = [_hr("="), f"  WATCHLIST — {len(rows)} symbols (ranked by ATLAS score)", _hr("=")]
    L.append(f"  {'SYMBOL':<10}{'SCORE':>7}  {'LABEL':<12}{'REGIME':<16}")
    for r in rows:
        if r.get("error"):
            L.append(f"  {r['symbol']:<10}{'ERR':>7}  {r['error'][:40]}")
        else:
            L.append(f"  {r['symbol']:<10}{r['atlas_score']:>7}  {str(r['label']):<12}{str(r['regime']):<16}")
    return "\n".join(L)


def format_seasonality(out: dict) -> str:
    L = [_hr("="), f"  SEASONALITY ({out.get('granularity','?')})", _hr("=")]
    L.append(f"  {'BUCKET':<8}{'MEAN%':>9}{'POS%':>8}{'N':>7}")
    for b in out.get("buckets", []):
        L.append(f"  {b['bucket']:<8}{b['mean_return_pct']:>9}{b['pct_positive']:>8}{b['samples']:>7}")
    for w in out.get("warnings", []):
        L.append(f"  ! {w}")
    return "\n".join(L)


def format_rebalance(out: dict) -> str:
    if out.get("error"):
        return f"REBALANCE ERROR: {out['error']}"
    L = [_hr("="), f"  REBALANCE — turnover {out.get('turnover')}  "
         f"({'action needed' if out.get('needs_rebalance') else 'within bands'})", _hr("=")]
    L.append(f"  {'SYMBOL':<10}{'CUR':>8}{'TGT':>8}{'DRIFT':>8}  ACTION")
    for t in out.get("trades", []):
        amt = f"  ({t['amount']})" if "amount" in t else ""
        L.append(f"  {t['symbol']:<10}{t['current_weight']:>8}{t['target_weight']:>8}{t['drift']:>8}  {t['action']}{amt}")
    return "\n".join(L)


def format_explain(out: dict) -> str:
    """A fuller layered narrative (Section 15) built from the analyze envelope."""
    if out.get("error"):
        return f"EXPLAIN ERROR for {out.get('symbol','?')}: {out['error']}"
    sig = out.get("_signal", {})
    L = [format_analysis({k: v for k, v in out.items() if k != "_signal"})]
    if sig and sig.get("direction") and sig.get("direction") != "flat":
        L.append("")
        L.append("THE TRADE (auto-proposed):")
        L.append(f"  {sig['direction'].upper()} — {sig.get('thesis','')}")
        L.append(f"  entry {sig['entry']} · stop {sig['stop']} · targets {sig.get('targets')} · "
                 f"R:R {sig.get('r_multiple')} · confidence {sig.get('confidence')}")
        L.append(f"  biggest risk: {sig.get('biggest_risk')}")
        L.append(f"  invalidation: {sig.get('what_would_make_me_wrong')}")
    elif sig:
        L.append("")
        L.append(f"THE TRADE: {sig.get('reason','no clean setup')}")
    return "\n".join(L)


def format_forecast(out: dict) -> str:
    """Horizon forecast: the distribution, its inputs, and its measured skill."""
    if out.get("error"):
        return f"FORECAST ERROR for {out.get('symbol','?')}: {out['error']}"
    if "results" in out:  # compare_methods envelope
        L = [_hr("="), f"  FORECAST METHOD COMPARISON — {out.get('symbol','?')} "
                       f"({out.get('horizon_days')}d)", _hr("=")]
        L.append(f"  {'METHOD':<10}{'MAPE%':>9}{'vs NAIVE':>10}{'DIR%':>8}{'COV80%':>9}{'N':>6}")
        for m in out.get("ranked_by_mape", []):
            r = out["results"][m]
            skill = r.get("skill_vs_naive")
            L.append(f"  {m:<10}{r.get('mape_pct'):>9}"
                     f"{('—' if skill is None else f'{skill*100:+.1f}%'):>10}"
                     f"{str(r.get('directional_accuracy_pct')):>8}"
                     f"{str(r.get('coverage_80_pct')):>9}{r.get('samples'):>6}")
        ranked = out.get("ranked_by_mape") or []
        best = ranked[0] if ranked else None
        if best == "naive":
            L.append("")
            L.append("  Nothing beat the random walk over these origins. On this symbol and horizon "
                     "the honest forecast is 'roughly today's price', with the interval doing the work.")
        elif best:
            L.append("")
            L.append(f"  Best: {best} — {out['results'][best].get('verdict','')}")
        return "\n".join(L)

    L: List[str] = [_hr("=")]
    sim = "  [SIMULATED DATA]" if out.get("simulated") else ""
    L.append(f"  {out['symbol']} — {out['horizon_days']}d forecast "
             f"({out['method']}/{out['model_version']}){sim}")
    L.append(f"  from {out.get('asof','?')}  ->  target {out.get('target_date','?')} "
             f"({out.get('horizon_bars')} trading bars)")
    L.append(_hr("="))
    L.append(f"  last close      {out['last_close']}")
    L.append(f"  forecast        {out['forecast_price']}   ({out['forecast_return_pct']:+.2f}%)"
             f"   <- {out['forecast_price_basis']}")
    L.append(f"  mean of dist    {out['expected_price']}")
    L.append(f"  80% interval    {out['interval_80']['low']} .. {out['interval_80']['high']}"
             f"   (width {out['interval_80_width_pct']}% of price)")
    L.append(f"  95% interval    {out['interval_95']['low']} .. {out['interval_95']['high']}")
    if out.get("prob_up") is not None:
        L.append(f"  P(close above today's price) {out['prob_up']:.1%}")
    c = out.get("components", {})
    L.append("")
    L.append("  MODEL INPUTS")
    L.append(f"    annualised volatility {c.get('sigma_annual_pct')}%   "
             f"horizon sigma {c.get('sigma_horizon')}")
    L.append(f"    raw drift {c.get('mu_raw_annual_pct')}%/yr shrunk by "
             f"{c.get('shrinkage_applied')} -> horizon drift {c.get('mu_horizon')}")
    L.append(f"    source: {c.get('drift_source')}   sample {c.get('sample_bars')} bars")
    sk = out.get("skill")
    if sk:
        L.append("")
        L.append("  MEASURED SKILL (walk-forward on this symbol's own history)")
        if sk.get("samples"):
            sv = sk.get("skill_vs_naive")
            skill_txt = "n/a" if sv is None else f"{sv * 100:+.1f}%"
            L.append(f"    MAPE {sk['mape_pct']}%  vs naive {sk['naive_mape_pct']}%  "
                     f"-> skill {skill_txt}")
            L.append(f"    direction right {sk.get('directional_accuracy_pct')}%   "
                     f"80% coverage {sk.get('coverage_80_pct')}%   "
                     f"95% coverage {sk.get('coverage_95_pct')}%   n={sk['samples']}")
        L.append(f"    {sk.get('verdict', sk.get('note',''))}")
        for w in sk.get("warnings", []):
            L.append(f"    ! {w}")
    for w in out.get("warnings", []):
        L.append(f"  ! {w}")
    L.append("")
    L.append("  " + out.get("disclaimer", ""))
    return "\n".join(L)


def format_daily(out: dict) -> str:
    from .daily import render_daily_text

    return render_daily_text(out)


def render(command: str, out: dict) -> str:
    """Dispatch to the right formatter; unknown shapes fall back to nothing."""
    return {
        "forecast": format_forecast,
        "daily": format_daily,
        "analyze": format_analysis,
        "signal": format_signal,
        "backtest": format_backtest,
        "option": format_option,
        "score": format_score,
        "watch": format_watch,
        "seasonality": format_seasonality,
        "rebalance": format_rebalance,
        "explain": format_explain,
    }.get(command, lambda o: "")(out)
