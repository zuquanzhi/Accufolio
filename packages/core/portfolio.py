"""Pure securities calculations with moving-average cost and transaction fees."""
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


def summarize_trades(trades, current_price):
    """Moving-average cost basis, with rows in chronological order."""
    if not isfinite(current_price) or current_price < 0:
        raise ValueError("current price must be finite and non-negative")
    shares = cost = realized = 0.0
    for trade in trades:
        quantity, price = float(trade['shares']), float(trade['price'])
        Lot(quantity, price)
        fee = float(trade['fee']) if 'fee' in trade.keys() else 0.0
        if not isfinite(fee) or fee < 0:
            raise ValueError('Transaction fee must be finite and non-negative')
        side = trade['side']
        if side == 'buy':
            shares += quantity
            cost += quantity * price + fee
        elif side == 'sell':
            if shares <= 0 or quantity > shares + 1e-9:
                raise ValueError('Insufficient shares for sell transaction')
            quantity = min(quantity, shares)
            basis = cost * quantity / shares
            realized += quantity * price - fee - basis
            shares -= quantity
            cost -= basis
            if shares < 1e-9:
                shares = cost = 0.0
        else:
            raise ValueError('Invalid trade side')
    value = shares * current_price
    return Portfolio(shares, cost, cost / shares if shares else None, value, value - cost,
                     (value - cost) / cost if cost else None), realized
