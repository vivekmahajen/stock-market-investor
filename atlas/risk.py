"""Risk management & position sizing (Section 9 of the ATLAS spec).

Risk is computed *before* upside. Every actionable idea gets a stop, a size
tied to a defined risk budget, and a worst-case loss in currency terms.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

Direction = Literal["long", "short"]


@dataclass
class PositionSize:
    units: float
    risk_amount: float          # currency risked (account * risk_pct)
    worst_case_loss: float      # currency loss if stop hit, incl. multiplier
    risk_per_unit: float
    risk_pct: float
    notional: float             # gross exposure at entry
    warnings: List[str]

    def to_dict(self) -> dict:
        return {
            "units": self.units,
            "risk_amount": round(self.risk_amount, 2),
            "worst_case_loss": round(self.worst_case_loss, 2),
            "risk_per_unit": round(self.risk_per_unit, 6),
            "risk_pct": self.risk_pct,
            "notional": round(self.notional, 2),
            "warnings": self.warnings,
        }


def size_position(
    account_equity: float,
    entry: float,
    stop: float,
    direction: Direction = "long",
    risk_pct: float = 0.01,
    multiplier: float = 1.0,
    max_risk_pct: float = 0.02,
    whole_units: bool = True,
    lot_size: float = 1.0,
) -> PositionSize:
    """Size = (account * risk%) / (per-unit risk * multiplier).

    Enforces the spec's defaults: risk per trade defaults to 1% and is hard
    capped (``max_risk_pct``, default 2%) unless the caller knowingly raises the
    cap. The stop must be on the correct side of entry for the direction.
    """
    if account_equity <= 0:
        raise ValueError("account_equity must be positive")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")

    warnings: List[str] = []
    if risk_pct > max_risk_pct:
        warnings.append(
            f"risk_pct {risk_pct:.2%} exceeds cap {max_risk_pct:.2%}; clamped. "
            "Raise max_risk_pct explicitly to override."
        )
        risk_pct = max_risk_pct

    risk_per_unit = (entry - stop) if direction == "long" else (stop - entry)
    if risk_per_unit <= 0:
        raise ValueError(
            f"Stop {stop} is on the wrong side of entry {entry} for a {direction} "
            "position (per-unit risk must be positive)."
        )

    risk_amount = account_equity * risk_pct
    raw_units = risk_amount / (risk_per_unit * multiplier)

    if whole_units:
        units = (raw_units // lot_size) * lot_size
    else:
        units = raw_units
    if units <= 0:
        warnings.append(
            "Computed size rounds to zero units — stop is too wide for this risk "
            "budget and price. Reduce entry-stop distance or raise the budget."
        )

    worst_case_loss = units * risk_per_unit * multiplier
    notional = units * entry * multiplier
    if notional > account_equity * 4:
        warnings.append(
            f"Notional {notional:,.0f} exceeds 4x equity — implies heavy leverage; "
            "confirm this is intended and permitted."
        )

    return PositionSize(
        units=units,
        risk_amount=risk_amount,
        worst_case_loss=worst_case_loss,
        risk_per_unit=risk_per_unit,
        risk_pct=risk_pct,
        notional=notional,
        warnings=warnings,
    )


def r_multiple(entry: float, stop: float, target: float, direction: Direction = "long") -> float:
    """Reward:risk ratio for a target. Returns reward divided by risk."""
    risk = (entry - stop) if direction == "long" else (stop - entry)
    if risk <= 0:
        raise ValueError("Invalid stop for direction; risk must be positive.")
    reward = (target - entry) if direction == "long" else (entry - target)
    return reward / risk


def meets_r_threshold(
    entry: float, stop: float, target: float, direction: Direction = "long", threshold: float = 1.5
) -> bool:
    """Whether a setup clears the minimum reward:risk (spec default >= 1.5-2.0)."""
    return r_multiple(entry, stop, target, direction) >= threshold


def portfolio_heat(open_risks: List[float], account_equity: float) -> dict:
    """Aggregate open risk ('heat') across positions as a fraction of equity.

    ``open_risks`` are the per-position worst-case losses in currency. Correlated
    exposure is the caller's responsibility to bucket before summing; this
    function reports total heat and flags when it is elevated.
    """
    if account_equity <= 0:
        raise ValueError("account_equity must be positive")
    total = sum(open_risks)
    pct = total / account_equity
    warnings: List[str] = []
    if pct > 0.06:
        warnings.append(f"Total portfolio heat {pct:.2%} exceeds 6% — concentrated risk.")
    return {"total_risk": round(total, 2), "heat_pct": round(pct, 4), "warnings": warnings}


def fractional_kelly(win_prob: float, win_loss_ratio: float, fraction: float = 0.5) -> dict:
    """Fractional Kelly stake with a capped fraction (spec: <= 1/2 Kelly).

    ``win_loss_ratio`` is average win / average loss (b). Returns the raw Kelly
    fraction and the capped stake, with an assumptions warning.
    """
    if not 0 <= win_prob <= 1:
        raise ValueError("win_prob must be in [0, 1]")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    fraction = min(max(fraction, 0.0), 0.5)  # never exceed half-Kelly
    b = win_loss_ratio
    kelly = (win_prob * (b + 1) - 1) / b
    kelly = max(kelly, 0.0)  # never recommend a negative-edge stake
    staked = kelly * fraction
    return {
        "kelly_fraction": round(kelly, 4),
        "applied_fraction": fraction,
        "staked_fraction": round(staked, 4),
        "warning": (
            "Kelly assumes a stable, known edge and win/loss ratio; real edges "
            "drift and are uncertain. Fractional Kelly reduces variance and ruin risk."
        ),
    }
