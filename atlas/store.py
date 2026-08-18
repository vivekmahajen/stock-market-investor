"""Persistent prediction store — the table every report is generated from.

A daily run writes one ``runs`` row and one ``predictions`` row per symbol. When
a forecast's target date arrives, :meth:`PredictionStore.resolve_due` fetches the
realised close and writes an ``outcomes`` row. Reports are then *generated from
the table*, not from a live re-computation — so a report is reproducible, and
last month's forecast can be judged against what actually happened.

Storage is SQLite via the standard library, keeping the package dependency-free.
The default database file is ``atlas_predictions.db`` in the working directory;
pass ``":memory:"`` for tests.

Tables
------
``runs``        one row per report run (universe, provider, model, timestamps)
``predictions`` one row per symbol per run — the forecast plus the analysis
                context that produced it, and the model's own measured skill
``outcomes``    one row per resolved prediction — realised price and the errors
``reports``     rendered report artefacts (text / markdown / html) kept with the
                run that produced them

Everything the agent later claims about accuracy comes out of ``outcomes``. If a
prediction has no outcome row, it is *unresolved* and must never be counted as a
hit or a miss.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
DEFAULT_DB = "atlas_predictions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    universe        TEXT NOT NULL,
    ranking_source  TEXT,
    provider        TEXT,
    timeframe       TEXT,
    lookback        INTEGER,
    horizon_days    INTEGER,
    method          TEXT,
    model_version   TEXT,
    symbol_count    INTEGER,
    simulated       INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    symbol                   TEXT NOT NULL,
    rank                     INTEGER,
    asof                     TEXT,
    target_date              TEXT,
    horizon_days             INTEGER,
    last_close               REAL,
    forecast_price           REAL,
    expected_price           REAL,
    forecast_return_pct      REAL,
    lo80                     REAL,
    hi80                     REAL,
    lo95                     REAL,
    hi95                     REAL,
    interval_80_width_pct    REAL,
    prob_up                  REAL,
    sigma_annual_pct         REAL,
    sigma_horizon            REAL,
    mu_horizon               REAL,
    method                   TEXT,
    model_version            TEXT,
    atlas_score              REAL,
    score_label              TEXT,
    regime                   TEXT,
    confluence               REAL,
    technical                REAL,
    fundamental              REAL,
    sentiment                REAL,
    relative_strength        REAL,
    risk                     REAL,
    signal_direction         TEXT,
    signal_confidence        REAL,
    skill_vs_naive           REAL,
    backtest_mape_pct        REAL,
    backtest_samples         INTEGER,
    directional_accuracy_pct REAL,
    coverage_80_pct          REAL,
    event_risk               TEXT,
    simulated                INTEGER NOT NULL DEFAULT 0,
    warnings                 TEXT,
    created_at               TEXT NOT NULL,
    UNIQUE(run_id, symbol)
);

CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id     INTEGER PRIMARY KEY REFERENCES predictions(id) ON DELETE CASCADE,
    resolved_at       TEXT NOT NULL,
    resolved_asof     TEXT,
    actual_price      REAL NOT NULL,
    actual_return_pct REAL,
    error_abs         REAL,
    error_pct         REAL,
    signed_error_pct  REAL,
    naive_error_pct   REAL,
    beat_naive        INTEGER,
    within_80         INTEGER,
    within_95         INTEGER,
    direction_correct INTEGER
);

CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    format     TEXT NOT NULL,
    title      TEXT,
    content    TEXT,
    path       TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pred_symbol      ON predictions(symbol);
CREATE INDEX IF NOT EXISTS idx_pred_target      ON predictions(target_date);
CREATE INDEX IF NOT EXISTS idx_pred_run         ON predictions(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_date        ON runs(run_date);
"""

#: Columns of ``predictions`` a caller may set, in insert order.
PREDICTION_COLUMNS = [
    "run_id", "symbol", "rank", "asof", "target_date", "horizon_days",
    "last_close", "forecast_price", "expected_price", "forecast_return_pct",
    "lo80", "hi80", "lo95", "hi95", "interval_80_width_pct", "prob_up",
    "sigma_annual_pct", "sigma_horizon", "mu_horizon",
    "method", "model_version",
    "atlas_score", "score_label", "regime", "confluence",
    "technical", "fundamental", "sentiment", "relative_strength", "risk",
    "signal_direction", "signal_confidence",
    "skill_vs_naive", "backtest_mape_pct", "backtest_samples",
    "directional_accuracy_pct", "coverage_80_pct",
    "event_risk", "simulated", "warnings", "created_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class PredictionStore:
    """SQLite-backed store of forecast runs, predictions, outcomes and reports."""

    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self._check_schema_version()
        self.conn.commit()

    # -- lifecycle --------------------------------------------------------
    def _check_schema_version(self) -> None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                              (str(SCHEMA_VERSION),))
            return
        found = int(row["value"])
        if found > SCHEMA_VERSION:
            raise RuntimeError(
                f"database '{self.path}' was written by a newer ATLAS schema (v{found} > "
                f"v{SCHEMA_VERSION}); upgrade ATLAS rather than risk silent data loss."
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PredictionStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes -----------------------------------------------------------
    def record_run(
        self,
        universe: str,
        horizon_days: int,
        method: str,
        model_version: str,
        provider: Optional[str] = None,
        timeframe: str = "1d",
        lookback: Optional[int] = None,
        ranking_source: Optional[str] = None,
        symbol_count: int = 0,
        simulated: bool = False,
        run_date: Optional[str] = None,
        notes: Optional[Iterable[str]] = None,
    ) -> int:
        """Insert a run header and return its id."""
        cur = self.conn.execute(
            """INSERT INTO runs (run_date, created_at, universe, ranking_source, provider,
                                 timeframe, lookback, horizon_days, method, model_version,
                                 symbol_count, simulated, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_date or _today(), _now(), universe, ranking_source, provider, timeframe,
             lookback, horizon_days, method, model_version, symbol_count,
             1 if simulated else 0, json.dumps(list(notes or []))),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_prediction(self, run_id: int, row: Dict[str, Any]) -> int:
        """Insert one prediction. Unknown keys are ignored; missing ones become NULL.

        Re-running the same symbol inside the same run replaces the earlier row
        (``UNIQUE(run_id, symbol)``), so a retried symbol does not duplicate.
        """
        data = {k: row.get(k) for k in PREDICTION_COLUMNS}
        data["run_id"] = run_id
        data["created_at"] = data.get("created_at") or _now()
        data["simulated"] = 1 if row.get("simulated") else 0
        if isinstance(data.get("warnings"), (list, tuple)):
            data["warnings"] = json.dumps(list(data["warnings"]))
        placeholders = ",".join("?" for _ in PREDICTION_COLUMNS)
        cur = self.conn.execute(
            f"INSERT OR REPLACE INTO predictions ({','.join(PREDICTION_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [data[c] for c in PREDICTION_COLUMNS],
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_report(self, run_id: Optional[int], fmt: str, content: str,
                      title: Optional[str] = None, path: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO reports (run_id, format, title, content, path, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, fmt, title, content, path, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # -- reads ------------------------------------------------------------
    def runs(self, limit: int = 50) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY run_date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._run_dict(r) for r in rows]

    def run(self, run_id: int) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_dict(r) if r else None

    def latest_run(self, universe: Optional[str] = None) -> Optional[dict]:
        if universe:
            r = self.conn.execute(
                "SELECT * FROM runs WHERE universe=? ORDER BY run_date DESC, id DESC LIMIT 1",
                (universe,)).fetchone()
        else:
            r = self.conn.execute(
                "SELECT * FROM runs ORDER BY run_date DESC, id DESC LIMIT 1").fetchone()
        return self._run_dict(r) if r else None

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["simulated"] = bool(d.get("simulated"))
        try:
            d["notes"] = json.loads(d.get("notes") or "[]")
        except (TypeError, ValueError):
            d["notes"] = []
        return d

    @staticmethod
    def _pred_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["simulated"] = bool(d.get("simulated"))
        try:
            d["warnings"] = json.loads(d.get("warnings") or "[]")
        except (TypeError, ValueError):
            d["warnings"] = []
        for key in ("within_80", "within_95", "direction_correct", "beat_naive"):
            if d.get(key) is not None:
                d[key] = bool(d[key])
        return d

    def predictions(self, run_id: Optional[int] = None, symbol: Optional[str] = None,
                    resolved: Optional[bool] = None, limit: int = 500) -> List[dict]:
        """Predictions left-joined to their outcomes — the row a report renders.

        ``resolved=True`` returns only predictions with an outcome, ``False``
        only those still open, ``None`` (default) returns both.
        """
        where, params = [], []
        if run_id is not None:
            where.append("p.run_id = ?")
            params.append(run_id)
        if symbol:
            where.append("p.symbol = ?")
            params.append(symbol.upper())
        if resolved is True:
            where.append("o.prediction_id IS NOT NULL")
        elif resolved is False:
            where.append("o.prediction_id IS NULL")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            f"""SELECT p.*, r.run_date, r.universe,
                       o.actual_price, o.actual_return_pct, o.error_pct, o.signed_error_pct,
                       o.naive_error_pct, o.beat_naive, o.within_80, o.within_95,
                       o.direction_correct, o.resolved_at, o.resolved_asof
                FROM predictions p
                JOIN runs r ON r.id = p.run_id
                LEFT JOIN outcomes o ON o.prediction_id = p.id
                {clause}
                ORDER BY p.run_id DESC, p.rank ASC, p.symbol ASC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [self._pred_dict(r) for r in rows]

    def symbol_history(self, symbol: str, limit: int = 100) -> List[dict]:
        """Every prediction ever made for ``symbol``, newest first."""
        return self.predictions(symbol=symbol, limit=limit)

    def due_predictions(self, asof: Optional[str] = None) -> List[dict]:
        """Unresolved predictions whose target date has arrived."""
        asof = asof or _today()
        rows = self.conn.execute(
            """SELECT p.* FROM predictions p
               LEFT JOIN outcomes o ON o.prediction_id = p.id
               WHERE o.prediction_id IS NULL AND p.target_date IS NOT NULL AND p.target_date <= ?
               ORDER BY p.target_date ASC""",
            (asof,),
        ).fetchall()
        return [self._pred_dict(r) for r in rows]

    def reports(self, run_id: Optional[int] = None, limit: int = 50) -> List[dict]:
        if run_id is None:
            rows = self.conn.execute(
                "SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM reports WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- outcome resolution ----------------------------------------------
    def resolve(self, prediction_id: int, actual_price: float,
                resolved_asof: Optional[str] = None) -> dict:
        """Score one prediction against the realised price and store the outcome."""
        p = self.conn.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
        if p is None:
            raise KeyError(f"no prediction with id {prediction_id}")
        if actual_price is None or actual_price <= 0:
            raise ValueError("actual_price must be a positive number")
        last, fc = p["last_close"], p["forecast_price"]
        error_abs = abs(fc - actual_price) if fc is not None else None
        error_pct = (error_abs / actual_price * 100) if error_abs is not None else None
        signed = ((fc - actual_price) / actual_price * 100) if fc is not None else None
        naive_pct = (abs(last - actual_price) / actual_price * 100) if last is not None else None
        beat = None if (error_pct is None or naive_pct is None) else int(error_pct < naive_pct)
        actual_ret = ((actual_price / last - 1) * 100) if last else None
        within80 = (int(p["lo80"] <= actual_price <= p["hi80"])
                    if p["lo80"] is not None and p["hi80"] is not None else None)
        within95 = (int(p["lo95"] <= actual_price <= p["hi95"])
                    if p["lo95"] is not None and p["hi95"] is not None else None)
        direction = None
        if fc is not None and last is not None and actual_price != last:
            direction = int((fc >= last) == (actual_price >= last))
        self.conn.execute(
            """INSERT OR REPLACE INTO outcomes
               (prediction_id, resolved_at, resolved_asof, actual_price, actual_return_pct,
                error_abs, error_pct, signed_error_pct, naive_error_pct, beat_naive,
                within_80, within_95, direction_correct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (prediction_id, _now(), resolved_asof, float(actual_price),
             _r(actual_ret), _r(error_abs), _r(error_pct), _r(signed), _r(naive_pct),
             beat, within80, within95, direction),
        )
        self.conn.commit()
        return {
            "prediction_id": prediction_id, "symbol": p["symbol"],
            "target_date": p["target_date"], "forecast_price": fc,
            "actual_price": round(float(actual_price), 4),
            "error_pct": _r(error_pct), "naive_error_pct": _r(naive_pct),
            "beat_naive": bool(beat) if beat is not None else None,
            "within_80": bool(within80) if within80 is not None else None,
            "within_95": bool(within95) if within95 is not None else None,
            "direction_correct": bool(direction) if direction is not None else None,
        }

    def resolve_due(self, registry, asof: Optional[str] = None, timeframe: str = "1d",
                    lookback: int = 400) -> dict:
        """Resolve every due prediction using prices fetched through ``registry``.

        The realised price used is the close of the **last bar at or before the
        target date** — never a later bar, and never today's price standing in
        for a target date that has not actually been reached in the data.
        """
        due = self.due_predictions(asof)
        resolved, skipped = [], []
        series_cache: Dict[str, Any] = {}
        for p in due:
            sym = p["symbol"]
            if sym not in series_cache:
                fetched = registry.get_ohlcv(sym, timeframe, lookback)
                series_cache[sym] = fetched.get("_series") if "error" not in fetched else None
                if series_cache[sym] is None:
                    skipped.append({"symbol": sym, "reason": fetched.get("error", "no data")})
            series = series_cache[sym]
            if series is None:
                continue
            price, bar_ts = _close_on_or_before(series, p["target_date"])
            if price is None:
                skipped.append({"symbol": sym, "prediction_id": p["id"],
                                "reason": f"no bar at or before target {p['target_date']} in the fetched history"})
                continue
            resolved.append(self.resolve(p["id"], price, resolved_asof=bar_ts))
        return {"due": len(due), "resolved": len(resolved), "skipped": skipped,
                "results": resolved}

    # -- aggregates -------------------------------------------------------
    def accuracy(self, symbol: Optional[str] = None, method: Optional[str] = None,
                 horizon_days: Optional[int] = None) -> dict:
        """Realised accuracy over resolved predictions. Honest about small n."""
        where, params = ["o.prediction_id IS NOT NULL"], []
        if symbol:
            where.append("p.symbol = ?")
            params.append(symbol.upper())
        if method:
            where.append("p.method = ?")
            params.append(method)
        if horizon_days:
            where.append("p.horizon_days = ?")
            params.append(horizon_days)
        row = self.conn.execute(
            f"""SELECT COUNT(*) n,
                       AVG(o.error_pct) mape,
                       AVG(o.naive_error_pct) naive_mape,
                       AVG(o.signed_error_pct) bias,
                       AVG(o.within_80) cov80,
                       AVG(o.within_95) cov95,
                       AVG(o.direction_correct) dir_acc,
                       AVG(o.beat_naive) beat_rate
                FROM predictions p JOIN outcomes o ON o.prediction_id = p.id
                WHERE {' AND '.join(where)}""",
            params,
        ).fetchone()
        n = row["n"] or 0
        open_n = self.conn.execute(
            """SELECT COUNT(*) n FROM predictions p LEFT JOIN outcomes o ON o.prediction_id=p.id
               WHERE o.prediction_id IS NULL"""
        ).fetchone()["n"]
        if n == 0:
            return {"resolved": 0, "open": open_n,
                    "note": ("No prediction has reached its target date yet — realised accuracy "
                             "is unknown. Do not quote a hit rate until this is non-zero.")}
        mape, naive = row["mape"], row["naive_mape"]
        skill = (1 - mape / naive) if (mape is not None and naive) else None
        out = {
            "resolved": n,
            "open": open_n,
            "symbol": symbol.upper() if symbol else "all",
            "mape_pct": _r(mape),
            "naive_mape_pct": _r(naive),
            "skill_vs_naive": round(skill, 4) if skill is not None else None,
            "mean_signed_error_pct": _r(row["bias"]),
            "coverage_80_pct": _r((row["cov80"] or 0) * 100),
            "coverage_95_pct": _r((row["cov95"] or 0) * 100),
            "directional_accuracy_pct": _r((row["dir_acc"] or 0) * 100) if row["dir_acc"] is not None else None,
            "beat_naive_rate_pct": _r((row["beat_rate"] or 0) * 100) if row["beat_rate"] is not None else None,
        }
        out["note"] = _accuracy_note(out)
        return out

    def leaderboard(self, min_resolved: int = 1) -> List[dict]:
        """Per-symbol realised accuracy, worst error last."""
        rows = self.conn.execute(
            """SELECT p.symbol,
                      COUNT(*) n,
                      AVG(o.error_pct) mape,
                      AVG(o.naive_error_pct) naive_mape,
                      AVG(o.direction_correct) dir_acc,
                      AVG(o.within_80) cov80
               FROM predictions p JOIN outcomes o ON o.prediction_id = p.id
               GROUP BY p.symbol HAVING n >= ?
               ORDER BY mape ASC""",
            (min_resolved,),
        ).fetchall()
        out = []
        for r in rows:
            mape, naive = r["mape"], r["naive_mape"]
            out.append({
                "symbol": r["symbol"], "resolved": r["n"],
                "mape_pct": _r(mape), "naive_mape_pct": _r(naive),
                "skill_vs_naive": round(1 - mape / naive, 4) if (mape is not None and naive) else None,
                "directional_accuracy_pct": _r((r["dir_acc"] or 0) * 100) if r["dir_acc"] is not None else None,
                "coverage_80_pct": _r((r["cov80"] or 0) * 100),
            })
        return out

    # -- export -----------------------------------------------------------
    def export_csv(self, run_id: Optional[int] = None, symbol: Optional[str] = None) -> str:
        """Predictions (+ outcomes where resolved) as CSV text."""
        rows = self.predictions(run_id=run_id, symbol=symbol, limit=10_000)
        if not rows:
            return ""
        cols = list(rows[0].keys())
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                        for k, v in r.items()})
        return buf.getvalue()

    def stats(self) -> dict:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "path": self.path,
            "exists_on_disk": self.path != ":memory:" and os.path.exists(self.path),
            "schema_version": SCHEMA_VERSION,
            "runs": q("SELECT COUNT(*) FROM runs"),
            "predictions": q("SELECT COUNT(*) FROM predictions"),
            "outcomes": q("SELECT COUNT(*) FROM outcomes"),
            "reports": q("SELECT COUNT(*) FROM reports"),
            "symbols_tracked": q("SELECT COUNT(DISTINCT symbol) FROM predictions"),
        }


def _r(v: Optional[float], places: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), places)


def _close_on_or_before(series, target_date: str):
    """(close, iso_ts) of the last bar at or before ``target_date``; (None, None) if none."""
    if not target_date:
        return None, None
    best = None
    for i, ts in enumerate(series.ts):
        if ts.date().isoformat() <= target_date:
            best = i
        else:
            break
    if best is None:
        return None, None
    # Only a bar that is genuinely at/after the target window has elapsed counts;
    # if the series simply ends before the target, the prediction is not yet due.
    last_ts = series.ts[-1].date().isoformat()
    if last_ts < target_date:
        return None, None
    return float(series.close[best]), series.ts[best].isoformat()


def _accuracy_note(o: dict) -> str:
    n = o["resolved"]
    if n < 10:
        return (f"Only {n} resolved prediction(s) — this is not yet evidence of anything. "
                f"Report it as a running tally, not as a hit rate.")
    parts = []
    skill = o.get("skill_vs_naive")
    if skill is not None:
        parts.append(
            f"error is {abs(skill)*100:.1f}% {'below' if skill > 0 else 'above'} a random walk"
        )
    cov = o.get("coverage_80_pct")
    if cov is not None:
        if cov < 70:
            parts.append(f"the 80% band only held {cov:.0f}% of the time — intervals are too narrow")
        elif cov > 92:
            parts.append(f"the 80% band held {cov:.0f}% of the time — intervals may be too wide")
        else:
            parts.append(f"80% band coverage is {cov:.0f}%, close to nominal")
    return "; ".join(parts) + "." if parts else "Resolved sample available."
