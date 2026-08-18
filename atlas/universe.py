"""Universe resolution for the daily report.

The daily-report guardrail is explicit: *constituents are resolved, not
recalled.* A "top 10" written from memory is exactly the kind of confident
fabrication the whole system is built to avoid. So this module always returns a
constituent list **with its ranking source stated** — either a dated static
snapshot committed to this file, or a live market-cap re-ranking pulled through a
fundamentals feed. The caller reports whichever was actually used.

The static snapshots are dated. They are a starting point that a live refresh
improves on, not a claim about today's market.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Dated static snapshots. Update the date when the membership is refreshed.
# NASDAQ-100 top 10 by market cap as of the snapshot date (approximate ordering);
# the live refresh reorders and is preferred when a fundamentals feed is present.
_SNAPSHOTS: Dict[str, Dict[str, object]] = {
    "nasdaq10": {
        "as_of": "2026-08-01",
        "description": "Ten largest NASDAQ listings by market capitalisation",
        "constituents": [
            "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
            "META", "AVGO", "TSLA", "COST", "NFLX",
        ],
    },
    "nasdaq5": {
        "as_of": "2026-08-01",
        "description": "Five largest NASDAQ listings by market capitalisation",
        "constituents": ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL"],
    },
}


def _parse_market_cap(raw) -> Optional[float]:
    """Coerce a fundamentals ``MarketCapitalization`` field to a float."""
    if raw is None:
        return None
    try:
        val = float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None


def get_universe(name: str = "nasdaq10", refresh: bool = False,
                 provider=None) -> Dict[str, object]:
    """Resolve a named universe to its constituents plus the ranking source.

    Parameters
    ----------
    name:
        Universe key (``"nasdaq10"`` by default).
    refresh:
        If True and ``provider`` exposes ``get_fundamentals``, re-rank the
        snapshot's candidate names by live market capitalisation. If the refresh
        cannot cover every candidate, it falls back to the static snapshot and
        records the fallback in ``notes`` — never a partial, silently narrowed
        universe.
    provider:
        A data provider (only used when ``refresh`` is True).

    Returns a dict with ``name``, ``constituents``, ``ranking_source``
    (``"static_snapshot"`` or ``"live_market_cap"``), ``as_of``, ``notes``.
    """
    key = name.lower().strip()
    snap = _SNAPSHOTS.get(key)
    if snap is None:
        return {
            "name": name,
            "error": f"unknown universe '{name}'. Known: {sorted(_SNAPSHOTS)}",
            "constituents": [],
            "ranking_source": None,
            "notes": [],
        }

    candidates: List[str] = list(snap["constituents"])
    result: Dict[str, object] = {
        "name": key,
        "constituents": list(candidates),
        "ranking_source": "static_snapshot",
        "as_of": snap["as_of"],
        "description": snap["description"],
        "notes": [],
    }

    if not refresh:
        return result

    if provider is None or not hasattr(provider, "get_fundamentals"):
        result["notes"].append(
            "live refresh requested but no fundamentals feed available; "
            "using dated static snapshot."
        )
        return result

    # Live re-rank by market cap over the snapshot's candidate names.
    caps: Dict[str, float] = {}
    failed: List[str] = []
    for sym in candidates:
        try:
            overview = provider.get_fundamentals(sym)
        except Exception as e:  # noqa: BLE001 - a failed name is reported, not fatal
            failed.append(f"{sym}: {e}")
            continue
        cap = _parse_market_cap((overview or {}).get("MarketCapitalization"))
        if cap is None:
            failed.append(f"{sym}: no MarketCapitalization")
        else:
            caps[sym] = cap

    if len(caps) < len(candidates):
        result["notes"].append(
            "live market-cap refresh could not cover all candidates "
            f"({len(caps)}/{len(candidates)}); falling back to dated static "
            "snapshot ordering. Missing: " + "; ".join(failed)
        )
        return result

    ranked = sorted(caps, key=lambda s: caps[s], reverse=True)
    result["constituents"] = ranked
    result["ranking_source"] = "live_market_cap"
    result["market_caps"] = {s: caps[s] for s in ranked}
    result["notes"].append(
        f"ranked by live market capitalisation via provider "
        f"'{getattr(provider, 'source', '?')}'."
    )
    return result


def list_universes() -> List[str]:
    return sorted(_SNAPSHOTS)
