"""Core data types for ATLAS.

Pure-standard-library dataclasses. No third-party runtime dependencies, so the
compute layer runs anywhere Python 3.9+ is available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"Inconsistent OHLC at {self.ts}: "
                f"O={self.open} H={self.high} L={self.low} C={self.close}"
            )
        if self.volume < 0:
            raise ValueError(f"Negative volume at {self.ts}: {self.volume}")


@dataclass(frozen=True)
class OHLCV:
    """An immutable OHLCV time series for one symbol/timeframe.

    Stores parallel arrays for fast columnar access. Construct via
    :meth:`from_bars` to get validation and chronological-order guarantees.
    """

    symbol: str
    timeframe: str
    ts: Sequence[datetime]
    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float]

    def __post_init__(self) -> None:
        n = len(self.ts)
        for name in ("open", "high", "low", "close", "volume"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"Column '{name}' length {len(getattr(self, name))} != ts length {n}")
        for i in range(1, n):
            if self.ts[i] <= self.ts[i - 1]:
                raise ValueError(f"Timestamps not strictly increasing at index {i}: {self.ts[i]}")

    def __len__(self) -> int:
        return len(self.ts)

    @classmethod
    def from_bars(cls, symbol: str, timeframe: str, bars: Sequence[Bar]) -> "OHLCV":
        bars = sorted(bars, key=lambda b: b.ts)
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            ts=[b.ts for b in bars],
            open=[b.open for b in bars],
            high=[b.high for b in bars],
            low=[b.low for b in bars],
            close=[b.close for b in bars],
            volume=[b.volume for b in bars],
        )

    def bar(self, i: int) -> Bar:
        return Bar(self.ts[i], self.open[i], self.high[i], self.low[i], self.close[i], self.volume[i])

    def tail(self, n: int) -> "OHLCV":
        n = max(0, min(n, len(self)))
        s = len(self) - n
        return OHLCV(
            symbol=self.symbol,
            timeframe=self.timeframe,
            ts=list(self.ts[s:]),
            open=list(self.open[s:]),
            high=list(self.high[s:]),
            low=list(self.low[s:]),
            close=list(self.close[s:]),
            volume=list(self.volume[s:]),
        )

    def slice(self, start: int, end: int) -> "OHLCV":
        """A contiguous sub-series over the half-open index range [start, end)."""
        start = max(0, start)
        end = min(len(self), end)
        return OHLCV(
            symbol=self.symbol,
            timeframe=self.timeframe,
            ts=list(self.ts[start:end]),
            open=list(self.open[start:end]),
            high=list(self.high[start:end]),
            low=list(self.low[start:end]),
            close=list(self.close[start:end]),
            volume=list(self.volume[start:end]),
        )

    @property
    def asof(self) -> Optional[datetime]:
        return self.ts[-1] if self.ts else None


@dataclass(frozen=True)
class Quote:
    """A point-in-time quote. ``staleness_seconds`` lets callers judge freshness."""

    symbol: str
    ts: datetime
    last: float
    bid: float
    ask: float
    volume: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> Optional[float]:
        mid = (self.ask + self.bid) / 2
        return (self.spread / mid) * 1e4 if mid > 0 else None

    def staleness_seconds(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        ref = self.ts if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc)
        return (now - ref).total_seconds()


@dataclass
class Provenance:
    """Records where a datum came from, for the ``data_provenance`` output field."""

    tool: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    asof: Optional[str] = None
    lookback: Optional[str] = None
    source: Optional[str] = None
    simulated: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
