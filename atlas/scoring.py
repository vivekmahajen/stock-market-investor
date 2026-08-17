"""Explainable multi-factor ATLAS Score (Section 10).

The score is a transparent, weighted blend of sub-scores with full attribution.
Weights are explicit and adjustable. A technical sub-score is computed directly
from indicators; fundamental/sentiment sub-scores are supplied by their tools
(the spec forbids inventing them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import indicators as ind
from .types import OHLCV

DEFAULT_WEIGHTS = {
    "technical": 0.35,
    "fundamental": 0.20,
    "sentiment": 0.15,
    "relative_strength": 0.15,
    "risk": 0.15,
}


@dataclass
class ScoreResult:
    score: float
    subscores: Dict[str, Optional[float]]
    weights: Dict[str, float]
    label: str
    contributors: List[str]
    horizon: str

    def to_dict(self) -> dict:
        return {
            "atlas_score": round(self.score, 1),
            "subscores": {k: (round(v, 1) if v is not None else None) for k, v in self.subscores.items()},
            "weights": self.weights,
            "label": self.label,
            "top_contributors": self.contributors,
            "horizon": self.horizon,
        }


def technical_subscore(series: OHLCV) -> Optional[float]:
    """0-100 technical sub-score from trend, momentum, and structure agreement.

    Deterministic and bounded. Returns ``None`` if the series is too short to
    compute the underlying indicators.
    """
    if len(series) < 60:
        return None
    close = list(series.close)
    ema20 = ind.ema(close, 20)
    ema50 = ind.ema(close, 50)
    rsi14 = ind.rsi(close, 14)
    macd = ind.macd(close)
    adx = ind.adx(series)

    last = len(series) - 1
    points = 0.0
    total = 0.0

    # Trend: price above/below EMAs, EMA stack.
    total += 1
    if ema20[last] is not None and ema50[last] is not None:
        if close[last] > ema20[last] > ema50[last]:
            points += 1
        elif close[last] > ema20[last]:
            points += 0.6
        elif close[last] < ema20[last] < ema50[last]:
            points += 0.0
        else:
            points += 0.4

    # Momentum: RSI regime (favour 45-70, penalise overbought/oversold extremes).
    total += 1
    r = rsi14[last]
    if r is not None:
        if 50 <= r <= 65:
            points += 1
        elif 45 <= r < 50 or 65 < r <= 72:
            points += 0.6
        elif r > 80 or r < 25:
            points += 0.1
        else:
            points += 0.4

    # MACD histogram sign.
    total += 1
    hist = macd["hist"][last]
    if hist is not None:
        points += 1 if hist > 0 else 0.2

    # Trend strength via ADX.
    total += 1
    a = adx["adx"][last]
    if a is not None:
        if a >= 25:
            points += 1
        elif a >= 20:
            points += 0.6
        else:
            points += 0.3

    return (points / total) * 100.0 if total else None


def relative_strength_subscore(series: OHLCV, benchmark: OHLCV, lookback: int = 63) -> Optional[float]:
    """Relative strength vs a benchmark over ``lookback`` bars, mapped to 0-100."""
    if len(series) <= lookback or len(benchmark) <= lookback:
        return None
    sym_ret = series.close[-1] / series.close[-1 - lookback] - 1.0
    bench_ret = benchmark.close[-1] / benchmark.close[-1 - lookback] - 1.0
    diff = sym_ret - bench_ret
    # Map a +/-20% relative move onto 0-100, clamped.
    return max(0.0, min(100.0, 50.0 + diff * 250.0))


def atlas_score(
    subscores: Dict[str, Optional[float]],
    weights: Optional[Dict[str, float]] = None,
    horizon: str = "next 4-8 weeks",
) -> ScoreResult:
    """Blend available sub-scores with re-normalised weights and attribute them.

    Missing (``None``) sub-scores are dropped and the remaining weights are
    renormalised, so the score is always on a 0-100 scale over what was measured.
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    present = {k: v for k, v in subscores.items() if v is not None and k in weights}
    if not present:
        raise ValueError("No sub-scores available to compute an ATLAS Score.")

    wsum = sum(weights[k] for k in present)
    norm = {k: weights[k] / wsum for k in present}
    score = sum(present[k] * norm[k] for k in present)

    # Attribution: rank contributions relative to a neutral 50 baseline.
    contribs = sorted(
        ((k, (present[k] - 50.0) * norm[k]) for k in present),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )
    contributors = [
        f"{'+' if v >= 0 else '-'} {k.replace('_', ' ')} ({present[k]:.0f})"
        for k, v in contribs[:4]
    ]

    return ScoreResult(
        score=score,
        subscores=subscores,
        weights={k: round(w, 3) for k, w in weights.items()},
        label=_label(score),
        contributors=contributors,
        horizon=horizon,
    )


_LABEL_THRESHOLDS = [(75, "buy"), (60, "accumulate"), (45, "hold"), (30, "reduce"), (0, "avoid")]


def _label(score: float) -> str:
    for thr, name in _LABEL_THRESHOLDS:
        if score >= thr:
            return name
    return "avoid"


# --------------------------------------------------------------------------- #
# I2. "What would change the score"
# --------------------------------------------------------------------------- #
def what_would_change(subscores: Dict[str, Optional[float]],
                      weights: Optional[Dict[str, float]] = None,
                      score: Optional[float] = None) -> dict:
    """Show the concrete factor moves that would upgrade or downgrade the label.

    Because the score is a renormalized weighted mean of the *present* factors,
    raising factor k by Δ moves the score by ``norm[k]·Δ``; this inverts that to
    report how far each factor must move to cross the nearest label boundary.
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    present = {k: v for k, v in subscores.items() if v is not None and k in weights}
    if not present:
        return {"note": "no sub-scores available"}
    if score is None:
        score = atlas_score(subscores, weights).score
    wsum = sum(weights[k] for k in present)
    norm = {k: weights[k] / wsum for k in present}

    up_thr = min((t for t, _ in _LABEL_THRESHOLDS if t > score), default=None)
    floor = max((t for t, _ in _LABEL_THRESHOLDS if t <= score), default=0)

    result = {"current_score": round(score, 1), "current_label": _label(score)}
    if up_thr is not None:
        needed = up_thr - score
        opts = []
        for k in present:
            delta = needed / norm[k]
            if present[k] + delta <= 100.5:
                opts.append({"factor": k, "current": round(present[k], 1),
                             "raise_by": round(delta, 1), "to": round(min(100.0, present[k] + delta), 1)})
        result["to_upgrade"] = {"target_label": _label(up_thr), "at_score": up_thr,
                                "options": sorted(opts, key=lambda o: o["raise_by"])[:3]}
    if floor > 0:
        drop = score - floor + 0.1
        dopts = []
        for k in present:
            delta = drop / norm[k]
            if present[k] - delta >= -0.5:
                dopts.append({"factor": k, "current": round(present[k], 1), "fall_by": round(delta, 1)})
        result["to_downgrade"] = {"target_label": _label(floor - 1), "below_score": floor,
                                  "options": sorted(dopts, key=lambda o: o["fall_by"])[:3]}
    result["biggest_drag"] = min(present, key=lambda k: (present[k] - 50) * norm[k])
    return result


# --------------------------------------------------------------------------- #
# I1. Probabilistic framing — in-sample score / forward-return study
# --------------------------------------------------------------------------- #
_SCORE_BANDS = [(0, 40), (40, 55), (55, 70), (70, 101)]


def score_forward_study(series: OHLCV, forward: int = 20, step: int = 2,
                        benchmark: Optional[OHLCV] = None) -> Optional[dict]:
    """In-sample study: bucket the technical score at each past bar by band and
    measure the forward return (vs a benchmark if given). Honest, in-sample, and
    labelled as such — not a universal base rate."""
    n = len(series)
    if n < 80 + forward:
        return None
    buckets = {(lo, hi): {"fwd": [], "hits": 0} for lo, hi in _SCORE_BANDS}
    for i in range(60, n - forward, max(1, step)):
        sc = technical_subscore(series.slice(0, i + 1))
        if sc is None or series.close[i] <= 0:
            continue
        fwd = (series.close[i + forward] / series.close[i] - 1.0) * 100.0
        beat = fwd > 0
        if benchmark is not None and len(benchmark) > i + forward and benchmark.close[i] > 0:
            bwd = (benchmark.close[i + forward] / benchmark.close[i] - 1.0) * 100.0
            beat = fwd > bwd
        for lo, hi in _SCORE_BANDS:
            if lo <= sc < hi:
                buckets[(lo, hi)]["fwd"].append(fwd)
                if beat:
                    buckets[(lo, hi)]["hits"] += 1
                break
    rows = []
    for lo, hi in _SCORE_BANDS:
        b = buckets[(lo, hi)]
        m = len(b["fwd"])
        if m == 0:
            continue
        rows.append({"band": f"{lo}-{min(hi, 100)}", "samples": m,
                     "pct_positive": round(b["hits"] / m * 100, 1),
                     "avg_forward_return_pct": round(sum(b["fwd"]) / m, 2)})
    return {"forward_bars": forward, "metric": "beat benchmark" if benchmark else "positive return",
            "bands": rows, "basis": "in-sample study on this series (not a universal base rate)"}


def probabilistic_framing(series: OHLCV, current_score: float, benchmark: Optional[OHLCV] = None,
                          forward: int = 20, step: int = 2) -> Optional[dict]:
    """A plain-language, sample-sized probability for the current score band."""
    study = score_forward_study(series, forward=forward, step=step, benchmark=benchmark)
    if not study:
        return None
    band = None
    for row in study["bands"]:
        lo, hi = (float(x) for x in row["band"].split("-"))
        if lo <= current_score <= hi:
            band = row
            break
    if not band:
        return {"study": study, "framing": "No in-sample history in the current score band."}
    framing = (f"In-sample on this series, bars in the {band['band']} score band {study['metric']} "
               f"{band['pct_positive']}% of the time over the next {forward} bars "
               f"(avg {band['avg_forward_return_pct']}%, sample={band['samples']}). In-sample only — "
               "not a promise about the future.")
    return {"framing": framing, "band": band, "study": study}
