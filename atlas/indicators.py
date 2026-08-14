"""Technical-indicator library (Section 4 of the ATLAS spec).

Pure-Python implementations of standard indicators. Every function returns a
list aligned to the input length, with ``None`` in the warm-up region so that
callers (e.g. the backtester) can index by bar without offset bugs.

Nothing here fabricates data: indicators are deterministic functions of the
OHLCV series they are given.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from .types import OHLCV

Number = Optional[float]


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")


def sma(values: Sequence[float], period: int) -> List[Number]:
    """Simple moving average."""
    _check_period(period)
    out: List[Number] = [None] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> List[Number]:
    """Exponential moving average, seeded with the SMA of the first ``period``."""
    _check_period(period)
    out: List[Number] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def wma(values: Sequence[float], period: int) -> List[Number]:
    """Linearly weighted moving average."""
    _check_period(period)
    out: List[Number] = [None] * len(values)
    denom = period * (period + 1) / 2
    for i in range(period - 1, len(values)):
        acc = 0.0
        for j in range(period):
            acc += values[i - period + 1 + j] * (j + 1)
        out[i] = acc / denom
    return out


def _wilder_smooth(values: Sequence[float], period: int) -> List[Number]:
    """Wilder's RMA smoothing (used by RSI/ATR/ADX)."""
    out: List[Number] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(close: Sequence[float], period: int = 14) -> List[Number]:
    """Relative Strength Index using Wilder's smoothing."""
    _check_period(period)
    n = len(close)
    out: List[Number] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        chg = close[i] - close[i - 1]
        gains[i] = max(chg, 0.0)
        losses[i] = max(-chg, 0.0)
    # Seed averages over the first `period` changes (indices 1..period).
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(close: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, and histogram."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line: List[Number] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    # Compute the signal EMA only over the defined region of the MACD line.
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: List[Number] = [None] * len(close)
    hist: List[Number] = [None] * len(close)
    if len(defined) >= signal:
        vals = [v for _, v in defined]
        sig_vals = ema(vals, signal)
        for (orig_i, _), sv in zip(defined, sig_vals):
            signal_line[orig_i] = sv
        for i in range(len(close)):
            if macd_line[i] is not None and signal_line[i] is not None:
                hist[i] = macd_line[i] - signal_line[i]
    return {"macd": macd_line, "signal": signal_line, "hist": hist}


def true_range(series: OHLCV) -> List[Number]:
    n = len(series)
    out: List[Number] = [None] * n
    if n == 0:
        return out
    out[0] = series.high[0] - series.low[0]
    for i in range(1, n):
        h, l, pc = series.high[i], series.low[i], series.close[i - 1]
        out[i] = max(h - l, abs(h - pc), abs(l - pc))
    return out


def atr(series: OHLCV, period: int = 14) -> List[Number]:
    """Average True Range (Wilder)."""
    tr = [v for v in true_range(series)]
    # true_range has no None except impossible cases; treat index 0 as valid.
    tr_vals = [0.0 if v is None else v for v in tr]
    smoothed = _wilder_smooth(tr_vals, period)
    return smoothed


def atr_percent(series: OHLCV, period: int = 14) -> List[Number]:
    a = atr(series, period)
    return [
        (v / c * 100.0) if (v is not None and c) else None
        for v, c in zip(a, series.close)
    ]


def bollinger_bands(close: Sequence[float], period: int = 20, num_std: float = 2.0):
    """Bollinger Bands with %B and bandwidth."""
    mid = sma(close, period)
    upper: List[Number] = [None] * len(close)
    lower: List[Number] = [None] * len(close)
    pct_b: List[Number] = [None] * len(close)
    bandwidth: List[Number] = [None] * len(close)
    for i in range(period - 1, len(close)):
        window = close[i - period + 1 : i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
        rng = upper[i] - lower[i]
        pct_b[i] = (close[i] - lower[i]) / rng if rng else None
        bandwidth[i] = rng / m if m else None
    return {"middle": mid, "upper": upper, "lower": lower, "pct_b": pct_b, "bandwidth": bandwidth}


def stochastic(series: OHLCV, k_period: int = 14, d_period: int = 3):
    """Stochastic oscillator %K and %D."""
    n = len(series)
    k: List[Number] = [None] * n
    for i in range(k_period - 1, n):
        hh = max(series.high[i - k_period + 1 : i + 1])
        ll = min(series.low[i - k_period + 1 : i + 1])
        rng = hh - ll
        k[i] = 100.0 * (series.close[i] - ll) / rng if rng else 50.0
    k_defined = [(i, v) for i, v in enumerate(k) if v is not None]
    d: List[Number] = [None] * n
    if len(k_defined) >= d_period:
        vals = [v for _, v in k_defined]
        d_vals = sma(vals, d_period)
        for (orig_i, _), dv in zip(k_defined, d_vals):
            d[orig_i] = dv
    return {"k": k, "d": d}


def adx(series: OHLCV, period: int = 14):
    """ADX with +DI / -DI (Wilder's directional system)."""
    n = len(series)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = series.high[i] - series.high[i - 1]
        down = series.low[i - 1] - series.low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr_vals = [0.0 if v is None else v for v in true_range(series)]
    atr_s = _wilder_smooth(tr_vals, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    plus_di: List[Number] = [None] * n
    minus_di: List[Number] = [None] * n
    dx: List[Number] = [None] * n
    for i in range(n):
        if atr_s[i] and plus_s[i] is not None and minus_s[i] is not None and atr_s[i] != 0:
            plus_di[i] = 100.0 * plus_s[i] / atr_s[i]
            minus_di[i] = 100.0 * minus_s[i] / atr_s[i]
            denom = plus_di[i] + minus_di[i]
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom else 0.0
    dx_defined = [(i, v) for i, v in enumerate(dx) if v is not None]
    adx_line: List[Number] = [None] * n
    if len(dx_defined) >= period:
        vals = [v for _, v in dx_defined]
        adx_vals = _wilder_smooth(vals, period)
        for (orig_i, _), av in zip(dx_defined, adx_vals):
            adx_line[orig_i] = av
    return {"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di}


def obv(series: OHLCV) -> List[Number]:
    """On-Balance Volume."""
    n = len(series)
    out: List[Number] = [None] * n
    if n == 0:
        return out
    acc = 0.0
    out[0] = 0.0
    for i in range(1, n):
        if series.close[i] > series.close[i - 1]:
            acc += series.volume[i]
        elif series.close[i] < series.close[i - 1]:
            acc -= series.volume[i]
        out[i] = acc
    return out


def roc(close: Sequence[float], period: int = 12) -> List[Number]:
    """Rate of Change (percent)."""
    _check_period(period)
    out: List[Number] = [None] * len(close)
    for i in range(period, len(close)):
        base = close[i - period]
        out[i] = (close[i] - base) / base * 100.0 if base else None
    return out


def vwap(series: OHLCV) -> List[Number]:
    """Cumulative volume-weighted average price over the given series."""
    n = len(series)
    out: List[Number] = [None] * n
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        tp = (series.high[i] + series.low[i] + series.close[i]) / 3.0
        cum_pv += tp * series.volume[i]
        cum_v += series.volume[i]
        out[i] = cum_pv / cum_v if cum_v else None
    return out


def relative_volume(series: OHLCV, period: int = 20) -> List[Number]:
    """Current volume relative to its moving average."""
    avg = sma(list(series.volume), period)
    return [
        (v / a) if (a and a > 0) else None for v, a in zip(series.volume, avg)
    ]
