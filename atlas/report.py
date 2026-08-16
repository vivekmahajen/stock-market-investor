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


def render(command: str, out: dict) -> str:
    """Dispatch to the right formatter; unknown shapes fall back to nothing."""
    return {
        "analyze": format_analysis,
        "signal": format_signal,
        "backtest": format_backtest,
        "option": format_option,
    }.get(command, lambda o: "")(out)
