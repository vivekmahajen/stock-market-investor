"""Calibration tracking (Appendix B — the actual differentiator).

Logs every signal's *stated* confidence and, once resolved, its realized
outcome. Then it measures whether the agent's probabilities are trustworthy:
reliability by confidence bucket, Brier score, and expected calibration error.

This is what turns "80% confidence" from a vibe into a number you can audit. The
spec (Sections 1, 6) demands calibration; this module makes it measurable.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

WIN = "win"
LOSS = "loss"
SCRATCH = "scratch"


@dataclass
class SignalRecord:
    id: str
    symbol: str
    direction: str
    confidence: float          # stated probability of a win, 0-100
    created: str
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    outcome: Optional[str] = None      # WIN | LOSS | SCRATCH
    realized_r: Optional[float] = None
    resolved: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class CalibrationLog:
    """JSON-file-backed log of signals and outcomes with calibration metrics."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._records: Dict[str, SignalRecord] = {}
        if path and os.path.exists(path):
            with open(path) as f:
                for d in json.load(f):
                    self._records[d["id"]] = SignalRecord(**d)

    def _save(self) -> None:
        if not self.path:
            return
        with open(self.path, "w") as f:
            json.dump([r.to_dict() for r in self._records.values()], f, indent=2)

    def log_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        created: str,
        entry: Optional[float] = None,
        stop: Optional[float] = None,
        target: Optional[float] = None,
        signal_id: Optional[str] = None,
    ) -> SignalRecord:
        if not 0 <= confidence <= 100:
            raise ValueError("confidence must be in [0, 100]")
        sid = signal_id or f"{symbol}:{len(self._records) + 1}"
        rec = SignalRecord(
            id=sid, symbol=symbol, direction=direction, confidence=confidence,
            created=created, entry=entry, stop=stop, target=target,
        )
        self._records[sid] = rec
        self._save()
        return rec

    def resolve(self, signal_id: str, outcome: str, realized_r: Optional[float] = None, resolved: str = "") -> SignalRecord:
        if outcome not in (WIN, LOSS, SCRATCH):
            raise ValueError(f"outcome must be one of {WIN}/{LOSS}/{SCRATCH}")
        rec = self._records.get(signal_id)
        if rec is None:
            raise KeyError(f"no signal '{signal_id}'")
        rec.outcome = outcome
        rec.realized_r = realized_r
        rec.resolved = resolved
        self._save()
        return rec

    def records(self) -> List[SignalRecord]:
        return list(self._records.values())

    def metrics(self, buckets: int = 5) -> dict:
        """Calibration report over resolved (non-scratch) signals.

        Returns per-bucket predicted-vs-realized hit rates, the Brier score, and
        the expected calibration error (ECE). Honest about small samples.
        """
        resolved = [r for r in self._records.values() if r.outcome in (WIN, LOSS)]
        n = len(resolved)
        if n == 0:
            return {"resolved": 0, "note": "No resolved signals yet — calibration is unknown."}

        # Brier score: mean((p - outcome)^2), p in [0,1], outcome in {0,1}.
        brier = 0.0
        edges = [i / buckets for i in range(buckets + 1)]
        rows = []
        ece = 0.0
        for b in range(buckets):
            lo, hi = edges[b], edges[b + 1]
            in_bucket = [r for r in resolved if lo <= r.confidence / 100.0 < hi or (b == buckets - 1 and r.confidence / 100.0 == 1.0)]
            if not in_bucket:
                rows.append({"bucket": f"{int(lo*100)}-{int(hi*100)}%", "count": 0,
                             "predicted": None, "realized": None})
                continue
            pred = sum(r.confidence / 100.0 for r in in_bucket) / len(in_bucket)
            realized = sum(1 for r in in_bucket if r.outcome == WIN) / len(in_bucket)
            rows.append({
                "bucket": f"{int(lo*100)}-{int(hi*100)}%",
                "count": len(in_bucket),
                "predicted": round(pred * 100, 1),
                "realized": round(realized * 100, 1),
            })
            ece += (len(in_bucket) / n) * abs(pred - realized)

        for r in resolved:
            p = r.confidence / 100.0
            o = 1.0 if r.outcome == WIN else 0.0
            brier += (p - o) ** 2
        brier /= n

        avg_r = None
        r_vals = [r.realized_r for r in resolved if r.realized_r is not None]
        if r_vals:
            avg_r = sum(r_vals) / len(r_vals)

        return {
            "resolved": n,
            "win_rate_pct": round(sum(1 for r in resolved if r.outcome == WIN) / n * 100, 1),
            "brier_score": round(brier, 4),
            "expected_calibration_error": round(ece, 4),
            "avg_realized_r": round(avg_r, 3) if avg_r is not None else None,
            "reliability_buckets": rows,
            "note": ("Sample is small; calibration estimates are noisy." if n < 30
                     else "Lower Brier and ECE mean better-calibrated confidence."),
        }
