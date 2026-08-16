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


# --------------------------------------------------------------------------- #
# Extended trend indicators (Section 4)
# --------------------------------------------------------------------------- #
def _reindex(raw, out_len, fn, min_len):
    """Apply ``fn`` to the defined (non-None) suffix of ``raw`` and re-scatter."""
    defined = [(i, v) for i, v in enumerate(raw) if v is not None]
    out: List[Number] = [None] * out_len
    if len(defined) >= min_len:
        result = fn([v for _, v in defined])
        for (orig_i, _), rv in zip(defined, result):
            out[orig_i] = rv
    return out


def hma(values, period: int) -> List[Number]:
    """Hull moving average: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    _check_period(period)
    half = max(1, period // 2)
    sqrt_p = max(1, int(math.sqrt(period)))
    wh, wf = wma(values, half), wma(values, period)
    raw = [(2 * h - f) if (h is not None and f is not None) else None for h, f in zip(wh, wf)]
    return _reindex(raw, len(values), lambda vals: wma(vals, sqrt_p), sqrt_p)


def vwma(series: OHLCV, period: int = 20) -> List[Number]:
    """Volume-weighted moving average."""
    _check_period(period)
    n = len(series)
    out: List[Number] = [None] * n
    for i in range(period - 1, n):
        num = sum(series.close[j] * series.volume[j] for j in range(i - period + 1, i + 1))
        den = sum(series.volume[j] for j in range(i - period + 1, i + 1))
        out[i] = num / den if den else None
    return out


def anchored_vwap(series: OHLCV, anchor_index: int = 0) -> List[Number]:
    """VWAP accumulated from ``anchor_index`` forward."""
    n = len(series)
    out: List[Number] = [None] * n
    cum_pv = cum_v = 0.0
    for i in range(max(0, anchor_index), n):
        tp = (series.high[i] + series.low[i] + series.close[i]) / 3.0
        cum_pv += tp * series.volume[i]
        cum_v += series.volume[i]
        out[i] = cum_pv / cum_v if cum_v else None
    return out


def supertrend(series: OHLCV, period: int = 10, multiplier: float = 3.0):
    """Supertrend line and direction (+1 up / -1 down) from ATR bands."""
    n = len(series)
    a = atr(series, period)
    f_up: List[Number] = [None] * n
    f_dn: List[Number] = [None] * n
    trend: List[Number] = [None] * n
    line: List[Number] = [None] * n
    for i in range(n):
        if a[i] is None:
            continue
        hl2 = (series.high[i] + series.low[i]) / 2.0
        bu, bd = hl2 + multiplier * a[i], hl2 - multiplier * a[i]
        if i == 0 or f_up[i - 1] is None:
            f_up[i], f_dn[i], trend[i], line[i] = bu, bd, 1, bd
            continue
        f_up[i] = bu if (bu < f_up[i - 1] or series.close[i - 1] > f_up[i - 1]) else f_up[i - 1]
        f_dn[i] = bd if (bd > f_dn[i - 1] or series.close[i - 1] < f_dn[i - 1]) else f_dn[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if series.close[i] < f_dn[i] else 1
        else:
            trend[i] = 1 if series.close[i] > f_up[i] else -1
        line[i] = f_dn[i] if trend[i] == 1 else f_up[i]
    return {"supertrend": line, "direction": trend}


def ichimoku(series: OHLCV, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
    """Ichimoku lines (unshifted: senkou spans plotted at the computed bar)."""
    n = len(series)
    def mid(p, i):
        return (max(series.high[i - p + 1:i + 1]) + min(series.low[i - p + 1:i + 1])) / 2.0
    ten: List[Number] = [None] * n
    kij: List[Number] = [None] * n
    sa: List[Number] = [None] * n
    sb: List[Number] = [None] * n
    for i in range(n):
        if i >= tenkan - 1:
            ten[i] = mid(tenkan, i)
        if i >= kijun - 1:
            kij[i] = mid(kijun, i)
        if ten[i] is not None and kij[i] is not None:
            sa[i] = (ten[i] + kij[i]) / 2.0
        if i >= senkou_b - 1:
            sb[i] = mid(senkou_b, i)
    return {"tenkan": ten, "kijun": kij, "senkou_a": sa, "senkou_b": sb, "chikou": list(series.close)}


def parabolic_sar(series: OHLCV, step: float = 0.02, max_step: float = 0.2) -> List[Number]:
    """Parabolic SAR (Wilder)."""
    n = len(series)
    sar: List[Number] = [None] * n
    if n < 2:
        return sar
    trend = 1
    af = step
    ep = series.high[0]
    cur = series.low[0]
    sar[0] = cur
    for i in range(1, n):
        cur = cur + af * (ep - cur)
        if trend == 1:
            cur = min(cur, series.low[i - 1], series.low[max(0, i - 2)])
            if series.high[i] > ep:
                ep, af = series.high[i], min(af + step, max_step)
            if series.low[i] < cur:
                trend, cur, ep, af = -1, ep, series.low[i], step
        else:
            cur = max(cur, series.high[i - 1], series.high[max(0, i - 2)])
            if series.low[i] < ep:
                ep, af = series.low[i], min(af + step, max_step)
            if series.high[i] > cur:
                trend, cur, ep, af = 1, ep, series.high[i], step
        sar[i] = cur
    return sar


def linreg_channel(values, period: int = 20, num_std: float = 2.0):
    """Linear-regression channel: fitted midline plus ±num_std residual bands."""
    _check_period(period)
    n = len(values)
    mid: List[Number] = [None] * n
    upper: List[Number] = [None] * n
    lower: List[Number] = [None] * n
    slope: List[Number] = [None] * n
    xs = list(range(period))
    mx = sum(xs) / period
    denom = sum((x - mx) ** 2 for x in xs)
    for i in range(period - 1, n):
        ys = values[i - period + 1:i + 1]
        my = sum(ys) / period
        b = sum((xs[k] - mx) * (ys[k] - my) for k in range(period)) / denom if denom else 0.0
        a = my - b * mx
        fit = [a + b * x for x in xs]
        resid = [ys[k] - fit[k] for k in range(period)]
        sd = math.sqrt(sum(r * r for r in resid) / period)
        mid[i], slope[i] = fit[-1], b
        upper[i], lower[i] = fit[-1] + num_std * sd, fit[-1] - num_std * sd
    return {"mid": mid, "upper": upper, "lower": lower, "slope": slope}


# --------------------------------------------------------------------------- #
# Extended momentum indicators
# --------------------------------------------------------------------------- #
def stoch_rsi(close, rsi_period: int = 14, stoch_period: int = 14, k_smooth: int = 3, d_smooth: int = 3):
    """Stochastic RSI (%K and %D)."""
    r = rsi(close, rsi_period)
    n = len(close)
    ksr: List[Number] = [None] * n
    for i in range(n):
        window = [r[j] for j in range(max(0, i - stoch_period + 1), i + 1) if r[j] is not None]
        if r[i] is None or len(window) < stoch_period:
            continue
        lo, hi = min(window), max(window)
        rng = hi - lo
        ksr[i] = (r[i] - lo) / rng * 100.0 if rng else 0.0
    k_line = _reindex(ksr, n, lambda vals: sma(vals, k_smooth), k_smooth)
    d_line = _reindex(k_line, n, lambda vals: sma(vals, d_smooth), d_smooth)
    return {"k": k_line, "d": d_line}


def cci(series: OHLCV, period: int = 20) -> List[Number]:
    """Commodity Channel Index."""
    n = len(series)
    out: List[Number] = [None] * n
    tp = [(series.high[i] + series.low[i] + series.close[i]) / 3.0 for i in range(n)]
    for i in range(period - 1, n):
        window = tp[i - period + 1:i + 1]
        ma = sum(window) / period
        mad = sum(abs(x - ma) for x in window) / period
        out[i] = (tp[i] - ma) / (0.015 * mad) if mad else 0.0
    return out


def williams_r(series: OHLCV, period: int = 14) -> List[Number]:
    """Williams %R (-100..0)."""
    n = len(series)
    out: List[Number] = [None] * n
    for i in range(period - 1, n):
        hh = max(series.high[i - period + 1:i + 1])
        ll = min(series.low[i - period + 1:i + 1])
        rng = hh - ll
        out[i] = -100.0 * (hh - series.close[i]) / rng if rng else 0.0
    return out


def tsi(close, long: int = 25, short: int = 13) -> List[Number]:
    """True Strength Index."""
    n = len(close)
    if n < 2:
        return [None] * n
    pc = [0.0] + [close[i] - close[i - 1] for i in range(1, n)]
    apc = [abs(x) for x in pc]

    def double_ema(vals):
        e1 = ema(vals, long)
        return _reindex(e1, n, lambda v: ema(v, short), short)

    ds_pc, ds_apc = double_ema(pc), double_ema(apc)
    return [
        (100.0 * ds_pc[i] / ds_apc[i]) if (ds_pc[i] is not None and ds_apc[i]) else None
        for i in range(n)
    ]


def mfi(series: OHLCV, period: int = 14) -> List[Number]:
    """Money Flow Index."""
    n = len(series)
    out: List[Number] = [None] * n
    tp = [(series.high[i] + series.low[i] + series.close[i]) / 3.0 for i in range(n)]
    rmf = [tp[i] * series.volume[i] for i in range(n)]
    for i in range(period, n):
        pos = neg = 0.0
        for j in range(i - period + 1, i + 1):
            if j == 0:
                continue
            if tp[j] > tp[j - 1]:
                pos += rmf[j]
            elif tp[j] < tp[j - 1]:
                neg += rmf[j]
        out[i] = 100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / neg)
    return out


def rsi_divergence(series: OHLCV, rsi_period: int = 14, left: int = 3, right: int = 3) -> List[dict]:
    """Detect RSI/price divergences at swing pivots (bullish and bearish)."""
    from .levels import swing_points

    r = rsi(list(series.close), rsi_period)
    sw = swing_points(series, left, right)
    out: List[dict] = []
    for a, b in zip(sw["lows"], sw["lows"][1:]):
        if r[a] is not None and r[b] is not None and series.low[b] < series.low[a] and r[b] > r[a]:
            out.append({"index": b, "type": "bullish", "rsi": round(r[b], 2)})
    for a, b in zip(sw["highs"], sw["highs"][1:]):
        if r[a] is not None and r[b] is not None and series.high[b] > series.high[a] and r[b] < r[a]:
            out.append({"index": b, "type": "bearish", "rsi": round(r[b], 2)})
    out.sort(key=lambda d: d["index"])
    return out


# --------------------------------------------------------------------------- #
# Extended volatility indicators
# --------------------------------------------------------------------------- #
def keltner_channels(series: OHLCV, period: int = 20, multiplier: float = 2.0, atr_period: int = 10):
    """Keltner channels: EMA midline ± multiplier*ATR."""
    mid = ema(list(series.close), period)
    a = atr(series, atr_period)
    n = len(series)
    upper: List[Number] = [None] * n
    lower: List[Number] = [None] * n
    for i in range(n):
        if mid[i] is not None and a[i] is not None:
            upper[i], lower[i] = mid[i] + multiplier * a[i], mid[i] - multiplier * a[i]
    return {"middle": mid, "upper": upper, "lower": lower}


def donchian_channels(series: OHLCV, period: int = 20):
    """Donchian channels: rolling highest-high / lowest-low / midline."""
    n = len(series)
    upper: List[Number] = [None] * n
    lower: List[Number] = [None] * n
    mid: List[Number] = [None] * n
    for i in range(period - 1, n):
        hh = max(series.high[i - period + 1:i + 1])
        ll = min(series.low[i - period + 1:i + 1])
        upper[i], lower[i], mid[i] = hh, ll, (hh + ll) / 2.0
    return {"upper": upper, "lower": lower, "middle": mid}


def historical_volatility(close, period: int = 20, annualize: int = 252) -> List[Number]:
    """Annualised historical volatility (%) from log returns."""
    n = len(close)
    out: List[Number] = [None] * n
    logret = [None] + [math.log(close[i] / close[i - 1]) if close[i - 1] > 0 else 0.0 for i in range(1, n)]
    for i in range(period, n):
        window = [x for x in logret[i - period + 1:i + 1] if x is not None]
        if len(window) < 2:
            continue
        m = sum(window) / len(window)
        var = sum((x - m) ** 2 for x in window) / (len(window) - 1)
        out[i] = math.sqrt(var) * math.sqrt(annualize) * 100.0
    return out


def choppiness_index(series: OHLCV, period: int = 14) -> List[Number]:
    """Choppiness index (100 = choppy/ranging, low = trending)."""
    n = len(series)
    out: List[Number] = [None] * n
    tr = [0.0 if v is None else v for v in true_range(series)]
    denom = math.log10(period)
    for i in range(period - 1, n):
        sum_tr = sum(tr[i - period + 1:i + 1])
        rng = max(series.high[i - period + 1:i + 1]) - min(series.low[i - period + 1:i + 1])
        if rng > 0 and sum_tr > 0 and denom:
            out[i] = 100.0 * math.log10(sum_tr / rng) / denom
    return out


# --------------------------------------------------------------------------- #
# Extended volume / flow indicators
# --------------------------------------------------------------------------- #
def _clv(series: OHLCV, i: int) -> float:
    rng = series.high[i] - series.low[i]
    if rng == 0:
        return 0.0
    return ((series.close[i] - series.low[i]) - (series.high[i] - series.close[i])) / rng


def ad_line(series: OHLCV) -> List[Number]:
    """Accumulation/Distribution line."""
    n = len(series)
    out: List[Number] = [None] * n
    acc = 0.0
    for i in range(n):
        acc += _clv(series, i) * series.volume[i]
        out[i] = acc
    return out


def cmf(series: OHLCV, period: int = 20) -> List[Number]:
    """Chaikin Money Flow."""
    n = len(series)
    out: List[Number] = [None] * n
    mfv = [_clv(series, i) * series.volume[i] for i in range(n)]
    for i in range(period - 1, n):
        vsum = sum(series.volume[i - period + 1:i + 1])
        out[i] = sum(mfv[i - period + 1:i + 1]) / vsum if vsum else 0.0
    return out


def vwap_bands(series: OHLCV, num_std: float = 2.0):
    """Cumulative VWAP with ±num_std deviation bands."""
    n = len(series)
    v = vwap(series)
    upper: List[Number] = [None] * n
    lower: List[Number] = [None] * n
    devs: List[float] = []
    for i in range(n):
        if v[i] is None:
            continue
        tp = (series.high[i] + series.low[i] + series.close[i]) / 3.0
        devs.append(tp - v[i])
        sd = math.sqrt(sum(d * d for d in devs) / len(devs))
        upper[i], lower[i] = v[i] + num_std * sd, v[i] - num_std * sd
    return {"vwap": v, "upper": upper, "lower": lower}


def volume_profile(series: OHLCV, bins: int = 20, value_area_pct: float = 0.70):
    """Volume-by-price: POC, value area, and high/low volume nodes."""
    n = len(series)
    if n == 0:
        return None
    lo, hi = min(series.low), max(series.high)
    if hi <= lo:
        return None
    width = (hi - lo) / bins
    vol = [0.0] * bins
    for i in range(n):
        tp = (series.high[i] + series.low[i] + series.close[i]) / 3.0
        b = min(bins - 1, max(0, int((tp - lo) / width)))
        vol[b] += series.volume[i]

    poc_bin = max(range(bins), key=lambda b: vol[b])
    total = sum(vol)
    target = total * value_area_pct
    lo_b = hi_b = poc_bin
    acc = vol[poc_bin]
    while acc < target and (lo_b > 0 or hi_b < bins - 1):
        left = vol[lo_b - 1] if lo_b > 0 else -1.0
        right = vol[hi_b + 1] if hi_b < bins - 1 else -1.0
        if right >= left:
            hi_b += 1
            acc += vol[hi_b]
        else:
            lo_b -= 1
            acc += vol[lo_b]
    avg = total / bins if bins else 0.0
    return {
        "poc": round(lo + (poc_bin + 0.5) * width, 4),
        "value_area_low": round(lo + lo_b * width, 4),
        "value_area_high": round(lo + (hi_b + 1) * width, 4),
        "hvn": [round(lo + (b + 0.5) * width, 4) for b in range(bins) if vol[b] > 1.5 * avg],
        "lvn": [round(lo + (b + 0.5) * width, 4) for b in range(bins) if 0 < vol[b] < 0.5 * avg],
    }


# --------------------------------------------------------------------------- #
# Relative-strength / cross-asset
# --------------------------------------------------------------------------- #
def _returns(series: OHLCV) -> List[float]:
    return [
        (series.close[i] / series.close[i - 1] - 1.0) if series.close[i - 1] else 0.0
        for i in range(1, len(series))
    ]


def beta(series: OHLCV, benchmark: OHLCV, period: int = 252) -> Number:
    """Beta of ``series`` vs ``benchmark`` over the last ``period`` returns."""
    ra, rb = _returns(series), _returns(benchmark)
    m = min(len(ra), len(rb), period)
    if m < 2:
        return None
    ra, rb = ra[-m:], rb[-m:]
    ma, mb = sum(ra) / m, sum(rb) / m
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(m)) / (m - 1)
    var = sum((rb[i] - mb) ** 2 for i in range(m)) / (m - 1)
    return cov / var if var else None


def correlation(series: OHLCV, benchmark: OHLCV, period: int = 252) -> Number:
    """Pearson correlation of returns vs a benchmark."""
    ra, rb = _returns(series), _returns(benchmark)
    m = min(len(ra), len(rb), period)
    if m < 2:
        return None
    ra, rb = ra[-m:], rb[-m:]
    ma, mb = sum(ra) / m, sum(rb) / m
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(m))
    va = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(m)))
    vb = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(m)))
    return cov / (va * vb) if va and vb else None


def rs_rating(series: OHLCV, benchmark: OHLCV, periods=(63, 126, 189, 252)) -> Number:
    """Relative-strength rating (1-99) vs a benchmark, recency-weighted."""
    ratios = []
    weights = []
    w = [2.0, 1.0, 1.0, 1.0]
    for k, p in enumerate(periods):
        if len(series) > p and len(benchmark) > p:
            s = series.close[-1] / series.close[-1 - p] - 1.0
            b = benchmark.close[-1] / benchmark.close[-1 - p] - 1.0
            ratios.append(s - b)
            weights.append(w[k] if k < len(w) else 1.0)
    if not ratios:
        return None
    rel = sum(r * wt for r, wt in zip(ratios, weights)) / sum(weights)
    return max(1, min(99, round(50 + rel * 100)))
