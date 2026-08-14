"""Alerts & monitoring (Section 13 of the ATLAS spec).

Alerts are **notifications, never auto-trades**. This module persists monitoring
rules and evaluates them against fresh data. It supports static and dynamic
(ATR-scaled, indicator, multi-condition) rules. When a rule triggers, the caller
is expected to re-run analysis before acting — the spec forbids acting on a bare
trigger.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from . import indicators as ind
from .types import OHLCV

# Supported condition kinds. Each is evaluated against a series in `evaluate()`.
CONDITION_KINDS = {
    "price_above",     # {"kind":"price_above","value":X}
    "price_below",
    "pct_change",      # {"kind":"pct_change","value":P,"lookback":N} -> |move| >= P%
    "rsi_above",       # {"kind":"rsi_above","value":X,"period":14}
    "rsi_below",
    "cross_above_ema", # {"kind":"cross_above_ema","period":N}
    "cross_below_ema",
    "atr_move",        # {"kind":"atr_move","mult":K,"period":14} -> move >= K*ATR from prior close
}


@dataclass
class Alert:
    id: str
    symbol: str
    condition: dict
    channel: str = "log"
    active: bool = True
    note: str = ""
    triggered_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class AlertStore:
    """JSON-file-backed store of alerts. In-memory if ``path`` is None."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._alerts: Dict[str, Alert] = {}
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        with open(self.path) as f:
            for d in json.load(f):
                self._alerts[d["id"]] = Alert(**d)

    def _save(self) -> None:
        if not self.path:
            return
        with open(self.path, "w") as f:
            json.dump([a.to_dict() for a in self._alerts.values()], f, indent=2)

    def create_alert(self, symbol: str, condition: dict, channel: str = "log", note: str = "") -> Alert:
        kind = condition.get("kind")
        if kind not in CONDITION_KINDS:
            raise ValueError(f"Unknown condition kind '{kind}'. Supported: {sorted(CONDITION_KINDS)}")
        alert_id = f"{symbol}:{kind}:{len(self._alerts) + 1}"
        alert = Alert(id=alert_id, symbol=symbol, condition=condition, channel=channel, note=note)
        self._alerts[alert_id] = alert
        self._save()
        return alert

    def list_alerts(self, active_only: bool = False) -> List[Alert]:
        return [a for a in self._alerts.values() if a.active or not active_only]

    def remove(self, alert_id: str) -> bool:
        existed = self._alerts.pop(alert_id, None) is not None
        self._save()
        return existed

    def evaluate(self, alert: Alert, series: OHLCV) -> dict:
        """Check one alert against a series. Returns ``{triggered, detail}``.

        Marks the alert triggered (and inactive) on a hit so it does not re-fire
        until re-armed. Re-running analysis before acting is the caller's job.
        """
        triggered, detail = _check(alert.condition, series)
        if triggered and alert.active:
            alert.triggered_at = series.asof.isoformat() if series.asof else "n/a"
            alert.active = False
            self._save()
        return {
            "id": alert.id,
            "symbol": alert.symbol,
            "triggered": triggered,
            "detail": detail,
            "reminder": "Alert is a notification, not a trade. Re-run analysis before acting.",
        }


def _check(cond: dict, series: OHLCV) -> tuple:
    kind = cond["kind"]
    close = list(series.close)
    last = len(series) - 1
    price = close[last]

    if kind == "price_above":
        return price > cond["value"], f"price {price:.4f} vs {cond['value']}"
    if kind == "price_below":
        return price < cond["value"], f"price {price:.4f} vs {cond['value']}"
    if kind == "pct_change":
        lb = cond.get("lookback", 1)
        if last < lb:
            return False, "insufficient history"
        move = (price / close[last - lb] - 1.0) * 100
        return abs(move) >= cond["value"], f"{move:.2f}% move over {lb} bars vs {cond['value']}%"
    if kind in ("rsi_above", "rsi_below"):
        r = ind.rsi(close, cond.get("period", 14))[last]
        if r is None:
            return False, "rsi undefined"
        if kind == "rsi_above":
            return r > cond["value"], f"rsi {r:.1f} vs {cond['value']}"
        return r < cond["value"], f"rsi {r:.1f} vs {cond['value']}"
    if kind in ("cross_above_ema", "cross_below_ema"):
        period = cond.get("period", 50)
        e = ind.ema(close, period)
        if last < 1 or e[last] is None or e[last - 1] is None:
            return False, "ema undefined"
        prev_above = close[last - 1] > e[last - 1]
        now_above = price > e[last]
        if kind == "cross_above_ema":
            return (not prev_above and now_above), f"close vs EMA{period}: {price:.2f}/{e[last]:.2f}"
        return (prev_above and not now_above), f"close vs EMA{period}: {price:.2f}/{e[last]:.2f}"
    if kind == "atr_move":
        period = cond.get("period", 14)
        mult = cond.get("mult", 1.0)
        a = ind.atr(series, period)[last]
        if a is None or last < 1:
            return False, "atr undefined"
        move = abs(price - close[last - 1])
        return move >= mult * a, f"move {move:.4f} vs {mult}*ATR {mult * a:.4f}"
    return False, f"unhandled kind {kind}"
