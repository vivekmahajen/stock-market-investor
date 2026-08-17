"""Paper-trading ledger (Section 3 ``paper_trade``).

A simulated broker: fills, positions, cash, and realized P&L. **Never places
real orders** — this is a bookkeeping simulation only. State can persist to a
JSON file so a CLI can maintain a book across invocations.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

BUY = "buy"
SELL = "sell"


@dataclass
class Position:
    symbol: str
    qty: float = 0.0          # negative = short
    avg_price: float = 0.0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "qty": self.qty, "avg_price": round(self.avg_price, 4)}


class PaperBroker:
    """Simulated broker with a cash balance, positions, and a trade log."""

    def __init__(self, starting_cash: float = 100_000.0, path: Optional[str] = None):
        self.path = path
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.realized_pnl = 0.0
        self.positions: Dict[str, Position] = {}
        self.trades: List[dict] = []
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        with open(self.path) as f:
            d = json.load(f)
        self.starting_cash = d.get("starting_cash", self.starting_cash)
        self.cash = d["cash"]
        self.realized_pnl = d["realized_pnl"]
        self.positions = {s: Position(**p) for s, p in d["positions"].items()}
        self.trades = d["trades"]

    def _save(self) -> None:
        if not self.path:
            return
        with open(self.path, "w") as f:
            json.dump(self.to_dict(include_trades=True), f, indent=2)

    def submit(self, symbol: str, side: str, qty: float, price: float) -> dict:
        """Fill an order. ``side`` is buy/sell; realized P&L is booked when a
        position is reduced or flipped."""
        if side not in (BUY, SELL):
            raise ValueError(f"side must be '{BUY}' or '{SELL}'")
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")
        symbol = symbol.upper()
        signed = qty if side == BUY else -qty
        pos = self.positions.get(symbol, Position(symbol))
        realized = 0.0

        if pos.qty == 0 or (pos.qty > 0) == (signed > 0):
            # Opening or adding in the same direction: weighted-average the cost.
            new_qty = pos.qty + signed
            pos.avg_price = (pos.avg_price * abs(pos.qty) + price * qty) / abs(new_qty) if new_qty else 0.0
            pos.qty = new_qty
        else:
            # Reducing / closing / flipping: realize P&L on the closed amount.
            closing = min(abs(signed), abs(pos.qty))
            direction = 1 if pos.qty > 0 else -1
            realized = (price - pos.avg_price) * closing * direction
            self.realized_pnl += realized
            new_qty = pos.qty + signed
            if new_qty == 0:
                pos.qty, pos.avg_price = 0.0, 0.0
            elif (new_qty > 0) == (pos.qty > 0):
                pos.qty = new_qty                      # reduced, same side; avg unchanged
            else:
                pos.qty, pos.avg_price = new_qty, price  # flipped to the other side

        self.cash -= signed * price  # buying spends cash, selling adds
        if pos.qty == 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = pos

        fill = {"symbol": symbol, "side": side, "qty": qty, "price": price,
                "realized_pnl": round(realized, 2), "cash_after": round(self.cash, 2)}
        self.trades.append(fill)
        self._save()
        return fill

    def equity(self, prices: Optional[Dict[str, float]] = None) -> float:
        """Cash plus mark-to-market value of open positions (needs prices)."""
        prices = prices or {}
        mtm = 0.0
        for s, p in self.positions.items():
            px = prices.get(s, p.avg_price)
            mtm += p.qty * px
        return self.cash + mtm

    def to_dict(self, prices: Optional[Dict[str, float]] = None, include_trades: bool = False) -> dict:
        d = {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "equity": round(self.equity(prices), 2),
            "note": "simulated paper account — no real orders are placed",
        }
        if include_trades:
            d["trades"] = self.trades
        return d

    def reset(self) -> None:
        self.cash = self.starting_cash
        self.realized_pnl = 0.0
        self.positions = {}
        self.trades = []
        self._save()
