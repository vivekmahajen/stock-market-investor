"""Named symbol universes — notably the NASDAQ top 10 by market capitalisation.

The daily report (Section 20) runs over a *universe*. A universe is never
invented at request time: it is either a **static, dated constituent list**
shipped here (honest about being a snapshot) or a **live re-ranking** computed
from a fundamentals feed's market-cap field. Both carry provenance so a report
can always say where its ten names came from.

Design note, in keeping with the no-fabrication guardrail: the static list is
stamped with the date it was last verified and is always reported as
``ranking_source: "static-snapshot"``. If the caller wants a genuinely current
ranking, :func:`resolve_universe` with ``refresh=True`` pulls market caps from
the provider and re-ranks a candidate pool — and if that feed is unavailable it
falls back to the snapshot **and says so** rather than pretending.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

#: Date the static constituent lists below were last verified by a human.
SNAPSHOT_ASOF = "2026-05-01"

#: NASDAQ's largest listings by market capitalisation, most-recent verified order.
#: A snapshot — re-rank with a live fundamentals feed for anything decision-grade.
NASDAQ_TOP10: List[str] = [
    "NVDA", "MSFT", "AAPL", "GOOGL", "AMZN",
    "META", "AVGO", "TSLA", "NFLX", "COST",
]

#: The pool searched when re-ranking by live market cap. Wider than ten so a
#: name that has climbed into the top ten since the snapshot can be found.
NASDAQ_MEGACAP_POOL: List[str] = NASDAQ_TOP10 + [
    "AMD", "PLTR", "CSCO", "ADBE", "PEP", "LIN", "TMUS", "INTU", "QCOM", "AMAT",
    "TXN", "ISRG", "BKNG", "AMGN", "HON", "MU", "ADP", "GILD", "VRTX", "LRCX",
]

#: Common index proxies for relative-strength benchmarking.
BENCHMARKS: Dict[str, str] = {
    "nasdaq100": "QQQ",
    "nasdaq_composite": "ONEQ",
    "sp500": "SPY",
}

UNIVERSES: Dict[str, List[str]] = {
    "nasdaq10": NASDAQ_TOP10,
    "nasdaq_megacap": NASDAQ_MEGACAP_POOL,
}


class UnknownUniverse(KeyError):
    """Raised when a caller names a universe that does not exist."""


def list_universes() -> Dict[str, int]:
    """Names of the built-in universes and how many symbols each holds."""
    return {name: len(syms) for name, syms in UNIVERSES.items()}


def static_universe(name: str = "nasdaq10", limit: Optional[int] = None) -> List[str]:
    """The shipped constituent list for ``name`` (a dated snapshot, not live)."""
    try:
        syms = UNIVERSES[name]
    except KeyError:  # pragma: no cover - defensive
        raise UnknownUniverse(
            f"unknown universe '{name}'; known: {', '.join(sorted(UNIVERSES))}"
        ) from None
    return list(syms[:limit] if limit else syms)


def _market_cap(overview: dict) -> Optional[float]:
    """Pull a numeric market cap out of a provider's company-overview dict."""
    for key in ("MarketCapitalization", "market_cap", "marketCap", "MarketCap"):
        raw = overview.get(key)
        if raw in (None, "", "None", "-"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def rank_by_market_cap(symbols: Sequence[str], registry, top: int = 10) -> dict:
    """Re-rank ``symbols`` by live market cap from the registry's fundamentals feed.

    Returns a dict with the ranked ``symbols``, the per-symbol ``market_caps``
    actually retrieved, and any ``errors``. Symbols whose cap could not be
    fetched are **excluded from the ranking** rather than assigned a guess; the
    caller decides whether the coverage is good enough.
    """
    caps: Dict[str, float] = {}
    errors: List[str] = []
    for sym in symbols:
        res = registry.get_fundamentals(sym)
        if "error" in res:
            errors.append(f"{sym}: {res['error']}")
            continue
        cap = _market_cap(res.get("overview") or {})
        if cap is None:
            errors.append(f"{sym}: overview had no usable MarketCapitalization field")
            continue
        caps[sym] = cap
    ranked = sorted(caps, key=lambda s: caps[s], reverse=True)[:top]
    return {
        "symbols": ranked,
        "market_caps": {s: caps[s] for s in ranked},
        "covered": len(caps),
        "requested": len(symbols),
        "errors": errors,
    }


def resolve_universe(
    name: str = "nasdaq10",
    registry=None,
    refresh: bool = False,
    limit: int = 10,
    pool: Optional[Sequence[str]] = None,
) -> dict:
    """Resolve a universe to a concrete, provenance-carrying symbol list.

    With ``refresh=False`` (the default) this returns the dated static snapshot.
    With ``refresh=True`` and a registry whose provider serves fundamentals, it
    re-ranks ``pool`` (default: the NASDAQ mega-cap pool) by market cap.

    A refresh that cannot cover at least ``limit`` symbols falls back to the
    snapshot and records the reason in ``notes`` — it never returns a partial
    ranking dressed up as a complete one.
    """
    notes: List[str] = []
    symbols = static_universe(name, limit=limit)
    ranking_source = "static-snapshot"
    market_caps: Optional[Dict[str, float]] = None
    asof = SNAPSHOT_ASOF

    if refresh:
        if registry is None:
            notes.append("refresh requested but no registry supplied; using the static snapshot.")
        else:
            candidates = list(pool) if pool else static_universe("nasdaq_megacap")
            ranked = rank_by_market_cap(candidates, registry, top=limit)
            if len(ranked["symbols"]) >= limit:
                symbols = ranked["symbols"]
                market_caps = ranked["market_caps"]
                ranking_source = "live-market-cap"
                asof = None  # stamped by the caller from the data's own asof
                if ranked["errors"]:
                    notes.append(
                        f"{len(ranked['errors'])} of {ranked['requested']} candidates had no "
                        f"market-cap data and were excluded from the ranking."
                    )
            else:
                notes.append(
                    f"live market-cap refresh covered only {ranked['covered']} of "
                    f"{ranked['requested']} candidates (needed {limit}); fell back to the "
                    f"{SNAPSHOT_ASOF} static snapshot."
                )
                if ranked["errors"]:
                    notes.append("refresh errors: " + "; ".join(ranked["errors"][:3]))

    if ranking_source == "static-snapshot":
        notes.append(
            f"Constituents are a static snapshot verified {SNAPSHOT_ASOF}, not a live "
            f"index feed. Index membership and market-cap order change; re-rank with "
            f"refresh=True against a fundamentals feed before treating the list as current."
        )

    return {
        "universe": name,
        "symbols": symbols,
        "count": len(symbols),
        "ranking_source": ranking_source,
        "ranking_asof": asof,
        "market_caps": market_caps,
        "notes": notes,
    }
