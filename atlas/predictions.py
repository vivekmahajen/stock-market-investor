"""A durable, auditable store of forecasts and their realised outcomes.

Every prediction the daily report makes is written here *before* the market can
prove it right or wrong. That ordering is the whole point: accuracy is measured
only from predictions that were committed ahead of time and later resolved
against a realised close — never from a walk-forward backtest dressed up as a
track record.

Storage is a single JSON file (a list of prediction records). It is deliberately
dependency-free and human-readable so a run can be audited by opening the file.

Lifecycle of a record:

1. **Logged** at forecast time with the horizon distribution and the anchor close.
2. **Resolved** once the data feed advances to (or past) the target date: the
   realised close is fetched, stored, and scored against both the model's median
   and the naive random-walk baseline (anchor close unchanged).
3. **Aggregated** by :func:`accuracy_stats` into realised MAPE, interval
   coverage, and directional accuracy — always reported with the resolved sample
   size, never inflated by open predictions.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional


def _today(asof: Optional[str] = None) -> date:
    if asof:
        return _to_date(asof)
    return datetime.now(timezone.utc).date()


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date() if ("T" in s or " " in s) \
        else datetime.strptime(s[:10], "%Y-%m-%d").date()


def _iso(d: date) -> str:
    return d.isoformat()


def target_date(asof, horizon_days: int) -> date:
    """The calendar date a forecast made on ``asof`` is judged against."""
    return _to_date(asof) + timedelta(days=horizon_days)


class PredictionStore:
    """JSON-backed list of prediction records with resolve/aggregate helpers."""

    def __init__(self, path: str = "atlas_predictions.json"):
        self.path = path
        self.records: List[dict] = []
        self._available = True
        self._load()

    # --- persistence -----------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            self.records = []
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.records = data if isinstance(data, list) else data.get("records", [])
        except (OSError, json.JSONDecodeError):
            # A corrupt/unreadable store must not crash a run; the caller notes it.
            self._available = False
            self.records = []

    def save(self) -> bool:
        """Persist to disk atomically. Returns False if the store is unavailable."""
        try:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.records, f, indent=2, default=str)
            os.replace(tmp, self.path)
            return True
        except OSError:
            self._available = False
            return False

    @property
    def available(self) -> bool:
        return self._available

    # --- logging ---------------------------------------------------------
    def log_prediction(self, *, run_id: str, symbol: str, asof, horizon_days: int,
                       last_close: float, median: float, interval_80: List[float],
                       interval_95: List[float], p_up: float, method: str,
                       skill_score: Optional[float] = None,
                       created: Optional[str] = None) -> dict:
        """Append one forecast record. Idempotent per (run_id, symbol)."""
        asof_d = _to_date(asof)
        tgt = target_date(asof_d, horizon_days)
        rec = {
            "id": f"{run_id}:{symbol}",
            "run_id": run_id,
            "symbol": symbol,
            "asof": _iso(asof_d),
            "horizon_days": horizon_days,
            "target_date": _iso(tgt),
            "last_close": last_close,
            "median": median,
            "interval_80": list(interval_80),
            "interval_95": list(interval_95),
            "p_up": p_up,
            "method": method,
            "skill_score": skill_score,
            "created": created or _iso(_today()),
            "resolved": False,
            "realized_close": None,
            "realized_date": None,
            "outcome": None,
        }
        # Replace any existing record with the same id (re-run of the same day).
        self.records = [r for r in self.records if r.get("id") != rec["id"]]
        self.records.append(rec)
        return rec

    # --- resolution ------------------------------------------------------
    def resolve(self, closes_by_symbol: Callable[[str], Optional[List[tuple]]],
                asof: Optional[str] = None) -> Dict[str, object]:
        """Resolve every open prediction whose target date the data has reached.

        ``closes_by_symbol(symbol)`` returns a list of ``(date, close)`` pairs
        (oldest first) or ``None`` if the symbol could not be fetched. A
        prediction resolves only when the series contains a bar on or after its
        target date — i.e. the horizon has genuinely elapsed *in the data*, not
        merely on the wall clock.
        """
        run_date = _today(asof)
        resolved_now = 0
        errors: List[str] = []
        # Cache fetches so many predictions on one symbol hit the feed once.
        cache: Dict[str, Optional[List[tuple]]] = {}
        for rec in self.records:
            if rec.get("resolved"):
                continue
            tgt = _to_date(rec["target_date"])
            sym = rec["symbol"]
            if sym not in cache:
                try:
                    cache[sym] = closes_by_symbol(sym)
                except Exception as e:  # noqa: BLE001
                    cache[sym] = None
                    errors.append(f"{sym}: {e}")
            series = cache[sym]
            if not series:
                continue
            realized = _realized_at(series, tgt)
            if realized is None:
                continue  # data has not reached the target date yet
            r_date, r_close = realized
            rec["resolved"] = True
            rec["realized_close"] = r_close
            rec["realized_date"] = _iso(r_date)
            rec["outcome"] = _score_outcome(rec, r_close)
            rec["resolved_on"] = _iso(run_date)
            resolved_now += 1
        return {
            "resolved_now": resolved_now,
            "open_remaining": sum(1 for r in self.records if not r.get("resolved")),
            "errors": errors,
        }

    # --- aggregation -----------------------------------------------------
    def resolved(self, symbol: Optional[str] = None,
                 horizon_days: Optional[int] = None) -> List[dict]:
        out = [r for r in self.records if r.get("resolved")]
        if symbol:
            out = [r for r in out if r["symbol"] == symbol.upper()]
        if horizon_days is not None:
            out = [r for r in out if r["horizon_days"] == horizon_days]
        return out

    def accuracy_stats(self, symbol: Optional[str] = None,
                       horizon_days: Optional[int] = None) -> Dict[str, object]:
        return accuracy_stats(self.resolved(symbol, horizon_days))


def _realized_at(series: List[tuple], tgt: date) -> Optional[tuple]:
    """First ``(date, close)`` on or after ``tgt``; None if data hasn't reached it."""
    best = None
    for d, c in series:
        dd = _to_date(d)
        if dd >= tgt:
            if best is None or dd < best[0]:
                best = (dd, c)
    return best


def _score_outcome(rec: dict, realized_close: float) -> dict:
    """Score one resolved prediction: errors, interval coverage, direction."""
    anchor = rec["last_close"]
    median = rec["median"]
    lo80, hi80 = rec["interval_80"]
    lo95, hi95 = rec["interval_95"]
    ape_model = abs(median - realized_close) / realized_close if realized_close else None
    ape_naive = abs(anchor - realized_close) / realized_close if realized_close else None
    pred_up = median >= anchor
    real_up = realized_close >= anchor
    return {
        "ape_model": ape_model,
        "ape_naive": ape_naive,
        "model_beat_naive": (ape_model is not None and ape_naive is not None
                             and ape_model < ape_naive),
        "within_80": lo80 <= realized_close <= hi80,
        "within_95": lo95 <= realized_close <= hi95,
        "predicted_up": pred_up,
        "realized_up": real_up,
        "direction_correct": pred_up == real_up,
        "realized_return": realized_close / anchor - 1.0 if anchor else None,
    }


def accuracy_stats(resolved: List[dict]) -> Dict[str, object]:
    """Aggregate realised accuracy over a list of resolved predictions.

    Under ~10 resolved predictions the result carries ``sufficient=False`` so the
    report can present a running tally instead of claiming a hit rate.
    """
    n = len(resolved)
    if n == 0:
        return {"resolved_count": 0, "sufficient": False,
                "note": "no predictions have resolved yet"}
    apes_m = [r["outcome"]["ape_model"] for r in resolved if r["outcome"]["ape_model"] is not None]
    apes_n = [r["outcome"]["ape_naive"] for r in resolved if r["outcome"]["ape_naive"] is not None]
    mape_model = 100.0 * sum(apes_m) / len(apes_m) if apes_m else None
    mape_naive = 100.0 * sum(apes_n) / len(apes_n) if apes_n else None
    cover80 = sum(1 for r in resolved if r["outcome"]["within_80"]) / n
    cover95 = sum(1 for r in resolved if r["outcome"]["within_95"]) / n
    dir_acc = sum(1 for r in resolved if r["outcome"]["direction_correct"]) / n
    beat = sum(1 for r in resolved if r["outcome"]["model_beat_naive"]) / n
    skill = (1.0 - mape_model / mape_naive) if (mape_model is not None
             and mape_naive not in (None, 0)) else None
    return {
        "resolved_count": n,
        "sufficient": n >= 10,
        "mape_model_pct": mape_model,
        "mape_naive_pct": mape_naive,
        "skill_vs_naive": skill,
        "model_beats_naive": bool(skill is not None and skill > 0),
        "coverage_80": cover80,
        "nominal_coverage_80": 0.80,
        "coverage_95": cover95,
        "nominal_coverage_95": 0.95,
        "directional_accuracy": dir_acc,
        "fraction_model_beat_naive": beat,
        "leaderboard": _leaderboard(resolved),
    }


def _leaderboard(resolved: List[dict]) -> List[dict]:
    """Per-symbol realised accuracy, best (lowest MAPE) first."""
    by_symbol: Dict[str, List[dict]] = {}
    for r in resolved:
        by_symbol.setdefault(r["symbol"], []).append(r)
    rows = []
    for sym, recs in by_symbol.items():
        apes = [r["outcome"]["ape_model"] for r in recs if r["outcome"]["ape_model"] is not None]
        dir_acc = sum(1 for r in recs if r["outcome"]["direction_correct"]) / len(recs)
        rows.append({
            "symbol": sym,
            "resolved_count": len(recs),
            "mape_model_pct": 100.0 * sum(apes) / len(apes) if apes else None,
            "directional_accuracy": dir_acc,
        })
    rows.sort(key=lambda x: (x["mape_model_pct"] is None, x["mape_model_pct"] or 0.0))
    return rows
