"""Signal journal & calibration wiring (Appendix B — the differentiator).

Logs every issued signal (with its *stated* confidence, entry, stop, first
target), then resolves the outcome from subsequent price data — win if the
target is reached before the stop, loss otherwise — and feeds the result into
the :class:`~atlas.calibration.CalibrationLog`. Over time this turns "70%
confidence" from a vibe into an audited number (Brier score, ECE, reliability
buckets).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .calibration import LOSS, WIN, CalibrationLog
from .types import OHLCV


def _outcome(series: OHLCV, start: int, end: int, entry: float, stop: float,
             target: float, direction: str):
    """Walk bars [start, end) and return (outcome, realized_r) or (None, None).

    If a single bar touches both stop and target, the stop is assumed first
    (conservative). Realized R is the reward:risk to the target on a win, -1 on
    a stop-out.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return None, None
    for i in range(start, end):
        hi, lo = series.high[i], series.low[i]
        if direction == "long":
            hit_stop, hit_tgt = lo <= stop, hi >= target
        else:
            hit_stop, hit_tgt = hi >= stop, lo <= target
        if hit_stop:
            return LOSS, -1.0
        if hit_tgt:
            return WIN, round(abs(target - entry) / risk, 3)
    return None, None


class SignalJournal:
    """Persistent journal of signals wired to a calibration log."""

    def __init__(self, path: Optional[str] = None):
        self.log = CalibrationLog(path)

    def record(self, signal: dict, created: Optional[str] = None) -> Optional[dict]:
        """Log a directional signal. Ignores ``flat`` signals and those missing
        the entry/stop/target needed to resolve later."""
        if signal.get("direction") in (None, "flat"):
            return None
        targets = signal.get("targets") or [None]
        if signal.get("entry") is None or signal.get("stop") is None or targets[0] is None:
            return None
        created = created or signal.get("asof") or ""
        rec = self.log.log_signal(
            symbol=signal["symbol"], direction=signal["direction"],
            confidence=float(signal.get("confidence") if signal.get("confidence") is not None else 50.0),
            created=created, entry=signal["entry"], stop=signal["stop"], target=targets[0],
        )
        return rec.to_dict()

    def resolve(self, symbol: str, series: OHLCV, horizon: Optional[int] = None) -> List[dict]:
        """Resolve any open signals for ``symbol`` using forward price data."""
        resolved = []
        for rec in self.log.records():
            if rec.symbol != symbol.upper() and rec.symbol != symbol:
                continue
            if rec.outcome is not None or rec.entry is None or rec.stop is None or rec.target is None:
                continue
            start = self._start_index(series, rec.created)
            if start is None:
                continue
            end = len(series) if horizon is None else min(len(series), start + horizon)
            outcome, r = _outcome(series, start, end, rec.entry, rec.stop, rec.target, rec.direction)
            if outcome:
                self.log.resolve(rec.id, outcome, r,
                                 resolved=series.asof.isoformat() if series.asof else "")
                resolved.append({"id": rec.id, "outcome": outcome, "realized_r": r})
        return resolved

    def metrics(self, buckets: int = 5) -> dict:
        return self.log.metrics(buckets)

    def records(self) -> List[dict]:
        return [r.to_dict() for r in self.log.records()]

    def _start_index(self, series: OHLCV, created: str) -> Optional[int]:
        """First bar at/after the signal's creation date (so we never resolve on
        data that predates the signal)."""
        if not created:
            return 0
        try:
            cdt = datetime.fromisoformat(created)
        except ValueError:
            return 0
        for i, ts in enumerate(series.ts):
            ts_cmp = ts if ts.tzinfo == cdt.tzinfo else ts.replace(tzinfo=cdt.tzinfo)
            if ts_cmp > cdt:  # strictly after creation — the next bar onward
                return i
        return None
