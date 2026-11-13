"""Pure portfolio calculations for buy-only lots."""
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class Lot:
    shares: float
    price: float

    def __post_init__(self):
        if not isfinite(self.shares) or not isfinite(self.price) or self.shares <= 0 or self.price <= 0:
            raise ValueError("shares and price must be finite and positive")

@dataclass(frozen=True)
class Portfolio:
    shares: float
    cost: float
    average_price: float | None
    value: float
    pnl: float
    return_rate: float | None

def summarize(lots: list[Lot], current_price: float) -> Portfolio:
    if not isfinite(current_price) or current_price < 0:
        raise ValueError("current price must be finite and non-negative")
    shares = sum(lot.shares for lot in lots)
    cost = sum(lot.shares * lot.price for lot in lots)
    value = shares * current_price
    pnl = value - cost
    return Portfolio(shares, cost, cost / shares if shares else None, value, pnl,
                     pnl / cost if cost else None)
