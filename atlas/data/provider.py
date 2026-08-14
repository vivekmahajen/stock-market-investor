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


class CSVProvider(DataProvider):
    """Loads OHLCV from CSV files named ``<SYMBOL>_<TIMEFRAME>.csv``.

    Expected header: ``ts,open,high,low,close,volume`` with ISO-8601 timestamps.
    This is real data (whatever you put in the files), so ``simulated`` is False.
    """

    simulated = False
    source = "csv"

    def __init__(self, directory: str):
        self.directory = directory

    def _path(self, symbol: str, timeframe: str) -> str:
        import os

        return os.path.join(self.directory, f"{symbol}_{timeframe}.csv")

    def get_ohlcv(self, symbol: str, timeframe: str, lookback: int) -> OHLCV:
        path = self._path(symbol, timeframe)
        bars: List[Bar] = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                bars.append(
                    Bar(
                        ts=datetime.fromisoformat(row["ts"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
        series = OHLCV.from_bars(symbol, timeframe, bars)
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

    def _rng(self, symbol: str, timeframe: str):
        # Seed deterministically from inputs so a (symbol, timeframe) is stable.
        state = (self.seed * 2654435761 + hash((symbol, timeframe))) & 0xFFFFFFFF

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
