"""Backtesting & metrics (Section 8 of the ATLAS spec).

A compact, honest, event-driven backtester. It models commission and slippage,
reports the full metric set, and — critically — never dresses up a tiny sample
as an edge. Callers pass a signal function; the engine handles fills, costs,
equity tracking, and statistics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional

from .types import OHLCV

# A strategy returns +1 (go/stay long), -1 (go/stay short), or 0 (flat) given
# the series and the current bar index. It must only look at data up to `i`
# (no look-ahead); the engine fills on the *next* bar's open.
SignalFn = Callable[[OHLCV, int], int]


@dataclass
class Trade:
    direction: Literal["long", "short"]
    entry_ts: object
    entry_price: float
    exit_ts: object
    exit_price: float
    units: float
    pnl: float
    return_pct: float
    bars_held: int


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity_curve: List[float]
    metrics: dict
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "metrics": self.metrics,
            "num_trades": len(self.trades),
            "warnings": self.warnings,
            "final_equity": round(self.equity_curve[-1], 2) if self.equity_curve else None,
        }


def run_backtest(
    series: OHLCV,
    signal_fn: SignalFn,
    starting_equity: float = 100_000.0,
    commission_bps: float = 1.0,
    slippage_bps: float = 2.0,
    allow_short: bool = True,
    periods_per_year: int = 252,
    warmup: int = 0,
) -> BacktestResult:
    """Run a next-bar-open, all-in position backtest with costs.

    Costs are charged on every entry and exit as ``(commission+slippage) bps``
    of notional. Position is fully invested when in a trade (size scales with
    equity). This is deliberately simple and transparent, not a production OMS.
    """
    n = len(series)
    warnings: List[str] = []
    equity = starting_equity
    equity_curve: List[float] = [starting_equity]
    trades: List[Trade] = []
    cost_rate = (commission_bps + slippage_bps) / 1e4

    position = 0          # -1, 0, +1
    entry_price = 0.0
    entry_i = 0
    units = 0.0

    def _fill(price: float, side: int) -> float:
        # Slippage/commission worsen the fill in the trade's direction.
        return price * (1 + side * cost_rate)

    for i in range(max(warmup, 0), n - 1):
        desired = signal_fn(series, i)
        if not allow_short and desired < 0:
            desired = 0
        fill_price = series.open[i + 1]  # act on next bar's open (no look-ahead)
        fill_ts = series.ts[i + 1]

        if position != 0 and desired != position:
            # Close the current position.
            exit_price = _fill(fill_price, -position)
            pnl = position * (exit_price - entry_price) * units
            equity += pnl
            ret = position * (exit_price - entry_price) / entry_price
            trades.append(
                Trade(
                    direction="long" if position == 1 else "short",
                    entry_ts=series.ts[entry_i + 1],
                    entry_price=entry_price,
                    exit_ts=fill_ts,
                    exit_price=exit_price,
                    units=units,
                    pnl=pnl,
                    return_pct=ret * 100.0,
                    bars_held=i - entry_i,
                )
            )
            position = 0

        if position == 0 and desired != 0:
            entry_price = _fill(fill_price, desired)
            units = equity / entry_price if entry_price > 0 else 0.0
            position = desired
            entry_i = i

        # Mark-to-market equity for the curve.
        mark = series.close[i + 1]
        if position != 0:
            unrealized = position * (mark - entry_price) * units
            equity_curve.append(equity + unrealized)
        else:
            equity_curve.append(equity)

    # Force-close at the last bar.
    if position != 0:
        exit_price = _fill(series.close[-1], -position)
        pnl = position * (exit_price - entry_price) * units
        equity += pnl
        ret = position * (exit_price - entry_price) / entry_price
        trades.append(
            Trade(
                direction="long" if position == 1 else "short",
                entry_ts=series.ts[entry_i + 1],
                entry_price=entry_price,
                exit_ts=series.ts[-1],
                exit_price=exit_price,
                units=units,
                pnl=pnl,
                return_pct=ret * 100.0,
                bars_held=(n - 1) - entry_i,
            )
        )
    equity_curve[-1] = equity

    metrics = _compute_metrics(trades, equity_curve, starting_equity, periods_per_year, n)
    if len(trades) < 30:
        warnings.append(
            f"Only {len(trades)} trades — sample is too small to trust. Metrics are "
            "indicative at best; treat any 'edge' here as unproven noise."
        )
    return BacktestResult(trades=trades, equity_curve=equity_curve, metrics=metrics, warnings=warnings)


def _compute_metrics(
    trades: List[Trade], equity_curve: List[float], start: float, ppy: int, n_bars: int
) -> dict:
    final = equity_curve[-1] if equity_curve else start
    total_return = (final / start - 1.0) if start else 0.0

    # Per-bar returns for risk-adjusted stats.
    bar_returns: List[float] = []
    for a, b in zip(equity_curve, equity_curve[1:]):
        bar_returns.append((b / a - 1.0) if a else 0.0)

    max_dd = _max_drawdown(equity_curve)
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    win_rate = len(wins) / n if n else 0.0
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    expectancy = (sum(t.pnl for t in trades) / n) if n else 0.0

    ann_factor = ppy
    mean_r = sum(bar_returns) / len(bar_returns) if bar_returns else 0.0
    std_r = _std(bar_returns)
    downside = _std([r for r in bar_returns if r < 0])
    sharpe = (mean_r / std_r * math.sqrt(ann_factor)) if std_r > 0 else 0.0
    sortino = (mean_r / downside * math.sqrt(ann_factor)) if downside > 0 else 0.0
    years = n_bars / ppy if ppy else 0.0
    cagr = ((final / start) ** (1 / years) - 1.0) if (years > 0 and start > 0 and final > 0) else 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0
    exposure = sum(t.bars_held for t in trades) / (n_bars - 1) if n_bars > 1 else 0.0

    return {
        "total_return_pct": round(total_return * 100.0, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "win_rate_pct": round(win_rate * 100.0, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": (round(profit_factor, 2) if profit_factor != math.inf else None),
        "expectancy_per_trade": round(expectancy, 2),
        "exposure_pct": round(exposure * 100.0, 2),
        "num_trades": n,
    }


def _max_drawdown(equity_curve: List[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = v / peak - 1.0
            max_dd = min(max_dd, dd)
    return max_dd


def _std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def verdict(metrics: dict, num_trades: int) -> str:
    """A blunt one-line read on whether the edge is plausibly real (Section 8)."""
    if num_trades < 30:
        return "likely noise — sample too small to conclude anything"
    sharpe = metrics.get("sharpe", 0.0)
    pf = metrics.get("profit_factor")
    if sharpe >= 1.0 and pf and pf >= 1.5:
        return "plausibly real — but confirm out-of-sample before trusting it"
    if sharpe >= 0.5:
        return "fragile — weak edge, sensitive to costs and regime"
    return "no edge — does not beat costs on this data"
