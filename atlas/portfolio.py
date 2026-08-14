"""Portfolio construction & optimization (Section 11 of the ATLAS spec).

Pure-Python mean/variance machinery — no numpy. Given aligned return series it
builds the covariance and correlation matrices and solves for weights under a
chosen objective, then reports the diversification, risk, and stress profile the
spec requires, always compared against a simple benchmark.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .types import OHLCV

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Small linear-algebra helpers (Gaussian elimination). Kept local & minimal.
# --------------------------------------------------------------------------- #
def _solve(matrix: List[List[float]], vec: List[float]) -> List[float]:
    """Solve A x = b for a small, well-conditioned symmetric A."""
    n = len(matrix)
    m = [row[:] + [vec[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-15:
            raise ValueError("Singular matrix in portfolio solve; add regularisation.")
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(n):
            if r != col:
                f = m[r][col] / pv
                for k in range(col, n + 1):
                    m[r][k] -= f * m[col][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _quad_form(w: List[float], cov: List[List[float]]) -> float:
    n = len(w)
    return sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def daily_returns(series: OHLCV) -> List[float]:
    out = []
    for i in range(1, len(series)):
        p = series.close[i - 1]
        out.append((series.close[i] / p - 1.0) if p else 0.0)
    return out


def _align(return_lists: List[List[float]]) -> List[List[float]]:
    """Trim all return series to the shortest common (most recent) length."""
    n = min(len(r) for r in return_lists)
    return [r[-n:] for r in return_lists]


def covariance_matrix(returns: List[List[float]], annualize: bool = True) -> List[List[float]]:
    k = len(returns)
    n = len(returns[0])
    means = [sum(r) / n for r in returns]
    scale = TRADING_DAYS if annualize else 1.0
    cov = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            c = sum((returns[i][t] - means[i]) * (returns[j][t] - means[j]) for t in range(n)) / (n - 1)
            cov[i][j] = cov[j][i] = c * scale
    return cov


def correlation_matrix(cov: List[List[float]]) -> List[List[float]]:
    k = len(cov)
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(k)]
    corr = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            corr[i][j] = cov[i][j] / (sd[i] * sd[j]) if sd[i] > 0 and sd[j] > 0 else 0.0
    return corr


# --------------------------------------------------------------------------- #
# Optimization
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioResult:
    symbols: List[str]
    weights: Dict[str, float]
    objective: str
    expected_return: float
    expected_vol: float
    sharpe: float
    diagnostics: dict

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "weights": {s: round(w, 4) for s, w in self.weights.items()},
            "expected_return_pct": round(self.expected_return * 100, 2),
            "expected_vol_pct": round(self.expected_vol * 100, 2),
            "sharpe": round(self.sharpe, 2),
            "diagnostics": self.diagnostics,
        }


def _long_only_normalize(w: List[float]) -> List[float]:
    w = [max(x, 0.0) for x in w]
    s = sum(w)
    if s <= 0:
        return [1.0 / len(w)] * len(w)
    return [x / s for x in w]


def optimize_portfolio(
    series_by_symbol: Dict[str, OHLCV],
    objective: str = "min_variance",
    risk_free_rate: float = 0.0,
    max_weight: Optional[float] = None,
    benchmark: Optional[OHLCV] = None,
    sectors: Optional[Dict[str, str]] = None,
    ridge: float = 1e-4,
) -> PortfolioResult:
    """Optimize weights under ``equal_weight``, ``inverse_vol``, ``min_variance``,
    or ``max_sharpe`` (long-only). Reports diversification, concentration, and a
    market-shock stress test alongside a benchmark comparison."""
    symbols = list(series_by_symbol.keys())
    if len(symbols) < 2:
        raise ValueError("Need at least two symbols to build a portfolio.")

    rets = _align([daily_returns(series_by_symbol[s]) for s in symbols])
    n = len(rets[0])
    if n < 30:
        raise ValueError(f"Only {n} aligned return observations — too few to estimate covariance.")

    mean_daily = [sum(r) / n for r in rets]
    ann_mean = [m * TRADING_DAYS for m in mean_daily]
    cov = covariance_matrix(rets, annualize=True)
    k = len(symbols)
    reg = [[cov[i][j] + (ridge if i == j else 0.0) for j in range(k)] for i in range(k)]

    if objective == "equal_weight":
        w = [1.0 / k] * k
    elif objective == "inverse_vol":
        inv = [1.0 / math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(k)]
        s = sum(inv)
        w = [x / s for x in inv] if s > 0 else [1.0 / k] * k
    elif objective == "min_variance":
        x = _solve(reg, [1.0] * k)
        w = _long_only_normalize(x)
    elif objective == "max_sharpe":
        excess = [ann_mean[i] - risk_free_rate for i in range(k)]
        x = _solve(reg, excess)
        w = _long_only_normalize(x)
    else:
        raise ValueError(f"Unknown objective '{objective}'.")

    if max_weight is not None:
        w = _apply_cap(w, max_weight)

    weights = dict(zip(symbols, w))
    exp_ret = sum(w[i] * ann_mean[i] for i in range(k))
    exp_vol = math.sqrt(max(_quad_form(w, cov), 0.0))
    sharpe = (exp_ret - risk_free_rate) / exp_vol if exp_vol > 0 else 0.0

    diagnostics = _diagnostics(symbols, w, cov, series_by_symbol, benchmark, sectors, rets)
    return PortfolioResult(symbols, weights, objective, exp_ret, exp_vol, sharpe, diagnostics)


def _apply_cap(w: List[float], cap: float) -> List[float]:
    """Iteratively cap weights and redistribute the excess (long-only)."""
    w = w[:]
    for _ in range(100):
        over = [i for i, x in enumerate(w) if x > cap + 1e-9]
        if not over:
            break
        excess = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        under = [i for i in range(len(w)) if w[i] < cap - 1e-9]
        room = sum(cap - w[i] for i in under)
        if room <= 0:
            break
        for i in under:
            w[i] += excess * (cap - w[i]) / room
    s = sum(w)
    return [x / s for x in w] if s > 0 else w


def _diagnostics(symbols, w, cov, series_by_symbol, benchmark, sectors, rets) -> dict:
    k = len(symbols)
    corr = correlation_matrix(cov)
    pairs = [corr[i][j] for i in range(k) for j in range(i + 1, k)]
    avg_corr = sum(pairs) / len(pairs) if pairs else 0.0
    max_corr = max(pairs) if pairs else 0.0

    warnings: List[str] = []
    max_w = max(w)
    if max_w > 0.40:
        warnings.append(f"Concentration: largest weight {max_w:.0%} exceeds 40%.")
    if avg_corr > 0.7:
        warnings.append(f"Low diversification: average pairwise correlation {avg_corr:.2f} is high.")

    sector_exposure: Dict[str, float] = {}
    if sectors:
        for s, wt in zip(symbols, w):
            sec = sectors.get(s, "unknown")
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + wt
        for sec, exp in sector_exposure.items():
            if exp > 0.5:
                warnings.append(f"Sector concentration: {sec} is {exp:.0%} of the book.")

    # Portfolio beta vs benchmark and a -10% market-shock stress test.
    beta = None
    stress = None
    if benchmark is not None:
        bench_ret = daily_returns(benchmark)
        n = min(len(bench_ret), len(rets[0]))
        bench_ret = bench_ret[-n:]
        aligned = [r[-n:] for r in rets]
        port_ret = [sum(w[i] * aligned[i][t] for i in range(k)) for t in range(n)]
        beta = _beta(port_ret, bench_ret)
        if beta is not None:
            stress = {"scenario": "market -10%", "estimated_portfolio_move_pct": round(beta * -10.0, 2)}

    return {
        "avg_pairwise_correlation": round(avg_corr, 3),
        "max_pairwise_correlation": round(max_corr, 3),
        "max_weight": round(max_w, 4),
        "effective_positions": round(1.0 / sum(x * x for x in w), 2) if any(w) else 0,
        "sector_exposure": {s: round(v, 4) for s, v in sector_exposure.items()} or None,
        "beta_vs_benchmark": round(beta, 3) if beta is not None else None,
        "stress_test": stress,
        "warnings": warnings,
    }


def _beta(port: List[float], bench: List[float]) -> Optional[float]:
    n = len(port)
    if n < 2:
        return None
    mp = sum(port) / n
    mb = sum(bench) / n
    cov = sum((port[t] - mp) * (bench[t] - mb) for t in range(n)) / (n - 1)
    var = sum((bench[t] - mb) ** 2 for t in range(n)) / (n - 1)
    return cov / var if var > 0 else None
