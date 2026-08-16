"""Event-risk calendar (Sections 2, 6, 12).

Turns an earnings calendar into forward-looking event risk: how many days until
the next report, and how severe the risk is. The spec is emphatic — never issue
a signal without checking the calendar, because earnings/macro can invalidate a
technical setup overnight. This module supplies that check; nothing is invented,
and an absent calendar yields an empty list, not a guess.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import List, Optional

# Days-until-event thresholds for the risk label.
_HIGH_DAYS = 7
_MEDIUM_DAYS = 21
_DEFAULT_WINDOW = 45


def parse_earnings_csv(text: str) -> List[dict]:
    """Parse an Alpha Vantage EARNINGS_CALENDAR CSV payload into raw rows.

    Expected header: ``symbol,name,reportDate,fiscalDateEnding,estimate,currency``.
    Returns ``[]`` for an empty (header-only) response. Malformed rows are
    skipped, not guessed.
    """
    stripped = text.lstrip("﻿").lstrip()
    if not stripped or not stripped.lower().startswith("symbol,"):
        # Empty feed or a non-CSV response — no usable rows.
        return []
    rows: List[dict] = []
    for row in csv.DictReader(io.StringIO(stripped)):
        report = (row.get("reportDate") or "").strip()
        if not report:
            continue
        rows.append({
            "symbol": (row.get("symbol") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "reportDate": report,
            "fiscalDateEnding": (row.get("fiscalDateEnding") or "").strip(),
            "estimate": (row.get("estimate") or "").strip() or None,
            "currency": (row.get("currency") or "").strip(),
        })
    return rows


def _risk_for(days_away: int) -> str:
    if days_away <= _HIGH_DAYS:
        return "high"
    if days_away <= _MEDIUM_DAYS:
        return "medium"
    return "low"


def build_event_risk(
    earnings_rows: List[dict],
    asof: date,
    window_days: int = _DEFAULT_WINDOW,
) -> List[dict]:
    """Convert raw earnings rows into forward event-risk entries.

    Keeps upcoming reports (``reportDate >= asof``) within ``window_days``,
    each labelled with days-away and a risk level, sorted soonest-first.
    """
    events: List[dict] = []
    for r in earnings_rows:
        try:
            rd = datetime.strptime(r["reportDate"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        days = (rd - asof).days
        if 0 <= days <= window_days:
            events.append({
                "type": "earnings",
                "date": rd.isoformat(),
                "days_away": days,
                "risk": _risk_for(days),
                "estimate": r.get("estimate"),
            })
    events.sort(key=lambda e: e["days_away"])
    return events


def nearest_event(events: List[dict]) -> Optional[dict]:
    return events[0] if events else None


def event_risk_note(events: List[dict]) -> Optional[str]:
    """A plain-language warning if the nearest event carries elevated risk."""
    ev = nearest_event(events)
    if not ev:
        return None
    if ev["risk"] == "high":
        return (
            f"Earnings in {ev['days_away']} day(s) on {ev['date']}: HIGH event risk — a "
            "technical setup can be invalidated overnight. Size down or defer until after."
        )
    if ev["risk"] == "medium":
        return (
            f"Earnings on {ev['date']} ({ev['days_away']} days out): moderate event risk — "
            "factor it into the holding window."
        )
    return None
