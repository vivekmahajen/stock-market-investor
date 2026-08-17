"""Data providers behind the Section 3 market-data tools.

The abstract :class:`DataProvider` is the seam where a real, low-latency feed
would plug in. Two reference implementations ship here:

* :class:`CSVProvider` — loads genuine historical OHLCV from CSV files on disk.
* :class:`SyntheticProvider` — a *deterministic, seeded* generator for demos and
  tests. Its output is explicitly flagged ``simulated=True`` in provenance so it
  can never be mistaken for real market data. This honours the spec's
  no-fabrication rule: simulated data is labelled, never passed off as real.
"""
from __future__ import annotations

import csv
import math
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..types import OHLCV, Bar, Provenance, Quote

_TIMEFRAME_DELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


class DataProvider(ABC):
    """Interface for market-data retrieval. Real feeds implement these."""

    simulated: bool = False
    source: str = "abstract"

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str, lookback: int) -> OHLCV: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote: ...

    def provenance(self, tool: str, symbol: str, timeframe: Optional[str], lookback: Optional[str]) -> Provenance:
        return Provenance(
            tool=tool,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            source=self.source,
            simulated=self.simulated,
        )


# Canonical field -> accepted header aliases (matched case-insensitively, spaces
# and underscores normalised). Lets files from Stooq (Date,Open,...), Alpha
# Vantage (timestamp,open,...), Yahoo (Date,...,Adj Close), and the native
# ts,open,... layout all load without manual editing.
_CSV_ALIASES = {
    "ts": ("ts", "date", "timestamp", "datetime", "time"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c", "last", "price", "closelast"),
    "adj_close": ("adjclose", "adjustedclose", "adjcloseprice"),
    "volume": ("volume", "vol", "v"),
}

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y", "%Y%m%d")


def _norm(name: str) -> str:
    return (
        name.strip().lower()
        .replace(" ", "").replace("_", "").replace(".", "").replace("/", "")
    )


def _num(value) -> float:
    """Parse a numeric cell, tolerating currency symbols, thousands commas, and
    stray quotes/whitespace (e.g. ``$305.93``, ``"1,234,500"``, ``495.40\\r``).
    Empty/sentinel cells raise."""
    if value is None:
        raise ValueError("empty numeric cell")
    s = str(value).strip().strip('"').strip("'").strip()
    s = s.replace("$", "").replace(",", "").replace("%", "").replace("\r", "").strip()
    if s == "" or s.upper() in ("N/D", "NULL", "NA", "N/A", "-", "--"):
        raise ValueError(f"non-numeric cell: {value!r}")
    return float(s)


def _parse_ts(value: str) -> datetime:
    value = value.strip().strip('"').strip("'").strip()
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp format: {value!r}")


def parse_ohlcv_csv(text: str, symbol: str, timeframe: str):
    """Parse a CSV string into an OHLCV series, auto-detecting the column layout.

    Returns ``(OHLCV, skipped_rows)``. Raises ``ValueError`` when required
    columns (date + OHLC) cannot be found. Malformed/sentinel rows are skipped,
    not guessed.
    """
    import io

    text = text.lstrip("﻿")  # strip a UTF-8 BOM if the file carried one
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"Empty or headerless CSV for '{symbol}'.")

    # Resolve each canonical field to its actual header name.
    normalised = {_norm(h): h for h in reader.fieldnames}
    resolved = {}
    for field, aliases in _CSV_ALIASES.items():
        for alias in aliases:
            if alias in normalised:
                resolved[field] = normalised[alias]
                break

    if "ts" not in resolved:
        raise ValueError(f"No date/timestamp column found in {reader.fieldnames} for '{symbol}'.")
    close_key = resolved.get("close") or resolved.get("adj_close")
    if not all(k in resolved for k in ("open", "high", "low")) or not close_key:
        raise ValueError(f"Missing OHLC columns in {reader.fieldnames} for '{symbol}'.")

    bars: List[Bar] = []
    skipped = 0
    first_error = None
    vol_key = resolved.get("volume")
    for row in reader:
        try:
            bars.append(
                Bar(
                    ts=_parse_ts(row[resolved["ts"]]),
                    open=_num(row[resolved["open"]]),
                    high=_num(row[resolved["high"]]),
                    low=_num(row[resolved["low"]]),
                    close=_num(row[close_key]),
                    volume=_num(row[vol_key]) if vol_key and row.get(vol_key) not in (None, "") else 0.0,
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            if first_error is None:
                first_error = f"{type(e).__name__}: {e} | row={row!r}"
            skipped += 1
            continue
    if not bars:
        raise ValueError(
            f"No usable data rows in CSV for '{symbol}'. "
            f"Detected columns: {resolved}. First-row failure: {first_error}"
        )
    return OHLCV.from_bars(symbol, timeframe, bars), skipped


class CSVProvider(DataProvider):
    """Loads OHLCV from CSV files in a directory, auto-detecting the layout.

    Accepts common column layouts (native ``ts,open,high,low,close,volume``,
    Stooq ``Date,Open,...``, Alpha Vantage ``timestamp,open,...``, Yahoo with
    ``Adj Close``) and a range of date formats, so browser-downloaded files load
    without editing. This is real data, so ``simulated`` is False.

    File resolution tries, in order: ``<SYMBOL>_<TIMEFRAME>.csv``, a lower-cased
    variant, and ``<SYMBOL>.csv`` — so a file simply named ``aapl.csv`` works.
    """

    simulated = False
    source = "csv"

    def __init__(self, directory: str):
        self.directory = directory
        self.last_skipped_rows = 0

    def _path(self, symbol: str, timeframe: str) -> str:
        import os

        candidates = [
            f"{symbol}_{timeframe}.csv",
            f"{symbol.lower()}_{timeframe.lower()}.csv",
            f"{symbol.upper()}_{timeframe}.csv",
            f"{symbol}.csv",
            f"{symbol.lower()}.csv",
            f"{symbol.upper()}.csv",
        ]
        for name in candidates:
            path = os.path.join(self.directory, name)
            if os.path.exists(path):
                return path
        raise FileNotFoundError(
            f"No CSV for '{symbol}' ({timeframe}) in {self.directory}. Looked for: "
            + ", ".join(dict.fromkeys(candidates))
        )

    def get_ohlcv(self, symbol: str, timeframe: str, lookback: int) -> OHLCV:
        # utf-8-sig transparently strips a BOM; errors='replace' avoids hard
        # failures on stray bytes so the row-level parser can report specifics.
        with open(self._path(symbol, timeframe), newline="", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        series, skipped = parse_ohlcv_csv(text, symbol, timeframe)
        self.last_skipped_rows = skipped
        return series.tail(lookback) if lookback else series

    def get_quote(self, symbol: str) -> Quote:
        # Derive a quote from the most recent daily bar available.
        series = self.get_ohlcv(symbol, "1d", 1)
        last = series.close[-1]
        spread = last * 0.0002
        return Quote(
            symbol=symbol,
            ts=series.ts[-1],
            last=last,
            bid=last - spread / 2,
            ask=last + spread / 2,
            volume=series.volume[-1],
        )


class SyntheticProvider(DataProvider):
    """Deterministic geometric-random-walk generator. SIMULATED data only.

    Uses a seeded linear-congruential generator so results are reproducible and
    never depend on ``random`` global state. Every series it returns is flagged
    ``simulated=True`` in provenance.
    """

    simulated = True
    source = "synthetic"

    def __init__(self, seed: int = 42, start_price: float = 100.0, annual_drift: float = 0.05, annual_vol: float = 0.20):
        self.seed = seed
        self.start_price = start_price
        self.annual_drift = annual_drift
        self.annual_vol = annual_vol

    @staticmethod
    def _stable_hash(s: str) -> int:
        # FNV-1a: deterministic across processes (unlike Python's salted hash()).
        h = 2166136261
        for ch in s:
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        return h

    def _rng(self, symbol: str, timeframe: str):
        # Seed deterministically from inputs so a (symbol, timeframe) is stable
        # across processes (Python's built-in hash() is per-process randomised).
        state = (self.seed * 2654435761 + self._stable_hash(f"{symbol}|{timeframe}")) & 0xFFFFFFFF

        def nxt() -> float:
            nonlocal state
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            return state / 0x7FFFFFFF

        return nxt

    def _gauss(self, u1: float, u2: float) -> float:
        u1 = min(max(u1, 1e-12), 1 - 1e-12)
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def get_ohlcv(self, symbol: str, timeframe: str, lookback: int) -> OHLCV:
        lookback = max(2, lookback)
        rng = self._rng(symbol, timeframe)
        delta = _TIMEFRAME_DELTA.get(timeframe, timedelta(days=1))
        ppy = timedelta(days=365) / delta if delta else 252
        dt = 1.0 / ppy
        mu = self.annual_drift
        sigma = self.annual_vol

        bars: List[Bar] = []
        price = self.start_price
        t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        for i in range(lookback):
            z = self._gauss(rng(), rng())
            ret = (mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z
            close = price * math.exp(ret)
            o = price
            hi = max(o, close) * (1 + 0.003 * rng())
            lo = min(o, close) * (1 - 0.003 * rng())
            vol = 1_000_000 * (0.5 + rng())
            bars.append(Bar(ts=t0 + delta * i, open=o, high=hi, low=lo, close=close, volume=vol))
            price = close
        return OHLCV.from_bars(symbol, timeframe, bars)

    def get_quote(self, symbol: str) -> Quote:
        series = self.get_ohlcv(symbol, "1d", 250)
        last = series.close[-1]
        spread = last * 0.0003
        return Quote(
            symbol=symbol,
            ts=series.ts[-1],
            last=last,
            bid=last - spread / 2,
            ask=last + spread / 2,
            volume=series.volume[-1],
        )
