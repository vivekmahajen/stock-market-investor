"""Options pricing & greeks (Section 3, ``get_options_chain`` context).

A pure Black-Scholes-Merton engine: European option prices, the full greek set
(delta, gamma, theta, vega, rho), and an implied-volatility solver. Fully
computable from inputs — no data feed required. Dividend yield ``q`` is
supported; set it to 0 for non-payers.

Conventions (the usual trading-desk scaling):
* theta is per **calendar day** (annual theta / 365)
* vega and rho are per **1 percentage point** move in vol / rate (raw / 100)
"""
from __future__ import annotations

import math
from typing import Dict, Optional

CALL = "call"
PUT = "put"


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float):
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def bs_price(S: float, K: float, T: float, r: float, sigma: float, kind: str = CALL, q: float = 0.0) -> float:
    """Black-Scholes-Merton price of a European option.

    ``T`` in years. At/after expiry or with zero vol, returns intrinsic value.
    """
    _validate(S, K)
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if kind == CALL else (K - S))
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if kind == CALL:
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    if kind == PUT:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)
    raise ValueError(f"kind must be '{CALL}' or '{PUT}', got {kind!r}")


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, kind: str = CALL, q: float = 0.0) -> Dict[str, float]:
    """Delta, gamma, theta (per day), vega (per 1% vol), rho (per 1% rate)."""
    _validate(S, K)
    if kind not in (CALL, PUT):
        raise ValueError(f"kind must be '{CALL}' or '{PUT}', got {kind!r}")
    if T <= 0 or sigma <= 0:
        # Degenerate: delta is the step function, other greeks vanish.
        itm = (S > K) if kind == CALL else (S < K)
        return {"delta": (1.0 if kind == CALL else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    pdf = _norm_pdf(d1)
    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)
    sqrt_t = math.sqrt(T)

    gamma = disc_q * pdf / (S * sigma * sqrt_t)
    vega = S * disc_q * pdf * sqrt_t / 100.0
    if kind == CALL:
        delta = disc_q * _norm_cdf(d1)
        theta = (-S * disc_q * pdf * sigma / (2 * sqrt_t)
                 - r * K * disc_r * _norm_cdf(d2)
                 + q * S * disc_q * _norm_cdf(d1)) / 365.0
        rho = K * T * disc_r * _norm_cdf(d2) / 100.0
    else:
        delta = -disc_q * _norm_cdf(-d1)
        theta = (-S * disc_q * pdf * sigma / (2 * sqrt_t)
                 + r * K * disc_r * _norm_cdf(-d2)
                 - q * S * disc_q * _norm_cdf(-d1)) / 365.0
        rho = -K * T * disc_r * _norm_cdf(-d2) / 100.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def implied_vol(
    price: float, S: float, K: float, T: float, r: float, kind: str = CALL, q: float = 0.0,
    tol: float = 1e-6, max_iter: int = 200,
) -> Optional[float]:
    """Solve for the volatility that reproduces ``price`` (bisection).

    Returns ``None`` if the price is outside no-arbitrage bounds (can't be
    matched by any non-negative vol).
    """
    _validate(S, K)
    if T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if kind == CALL else (K - S))
    upper_bound = S * math.exp(-q * T) if kind == CALL else K * math.exp(-r * T)
    if price < intrinsic - tol or price > upper_bound + tol:
        return None

    lo, hi = 1e-6, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        diff = bs_price(S, K, T, r, mid, kind, q) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def option_analysis(
    S: float, K: float, T: float, r: float, kind: str = CALL, q: float = 0.0,
    sigma: Optional[float] = None, price: Optional[float] = None,
) -> dict:
    """Full option workup. Provide ``sigma`` to price, or ``price`` to imply vol."""
    if sigma is None and price is None:
        raise ValueError("Provide either sigma (to price) or price (to imply vol).")
    iv = None
    if sigma is None:
        iv = implied_vol(price, S, K, T, r, kind, q)
        if iv is None:
            return {"error": "price outside no-arbitrage bounds; cannot imply volatility"}
        sigma = iv
    px = bs_price(S, K, T, r, sigma, kind, q)
    greeks = bs_greeks(S, K, T, r, sigma, kind, q)
    moneyness = "ITM" if ((S > K) if kind == CALL else (S < K)) else ("ATM" if abs(S - K) / K < 0.005 else "OTM")
    return {
        "kind": kind, "spot": S, "strike": K, "T_years": round(T, 5),
        "rate": r, "dividend_yield": q, "sigma": round(sigma, 6),
        "implied_vol": round(iv, 6) if iv is not None else None,
        "price": round(px, 4),
        "greeks": {k: round(v, 6) for k, v in greeks.items()},
        "moneyness": moneyness,
    }


def _validate(S: float, K: float) -> None:
    if S <= 0 or K <= 0:
        raise ValueError("spot and strike must be positive")


def build_chain(spot: float, expiries_days, sigma: float, r: float = 0.04, q: float = 0.0,
                n_strikes: int = 5, strike_step_pct: float = 0.05) -> dict:
    """Build a model-generated options chain (Black-Scholes at ``sigma``).

    Strikes are placed symmetrically around ``spot``; each is priced (call & put)
    with full greeks for each expiry in ``expiries_days``. This is a *computed*
    chain — it is not live market implied vol or open interest, and says so.
    """
    if spot <= 0 or sigma <= 0:
        raise ValueError("spot and sigma must be positive")
    strikes = [round(spot * (1 + k * strike_step_pct), 2) for k in range(-n_strikes, n_strikes + 1)]
    expiries = []
    for dte in expiries_days:
        T = dte / 365.0
        rows = []
        for K in strikes:
            call = option_analysis(spot, K, T, r, CALL, q, sigma=sigma)
            put = option_analysis(spot, K, T, r, PUT, q, sigma=sigma)
            rows.append({
                "strike": K,
                "call": {"price": call["price"], "greeks": call["greeks"]},
                "put": {"price": put["price"], "greeks": put["greeks"]},
                "call_moneyness": call["moneyness"],
            })
        expiries.append({"dte": dte, "T_years": round(T, 4), "strikes": rows})
    return {
        "spot": round(spot, 4), "sigma": round(sigma, 4), "rate": r, "dividend_yield": q,
        "generated": True,
        "note": "model-generated chain (Black-Scholes at the given/realized vol); "
                "not live market implied vol or open interest",
        "expiries": expiries,
    }
