"""Deterministic monthly-contribution simulations; inputs are daily closing prices."""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from .metrics import xirr

@dataclass(frozen=True)
class BacktestResult:
    invested: float
    shares: float
    cash: float
    value: float
    return_rate: float
    xirr: float | None
    equity: tuple[tuple[date, float], ...]

def run(prices: list[tuple[date, float]], monthly: float, strategy: str = 'fixed', threshold: float = .05, lookback: int = 20, cooldown: int = 5) -> BacktestResult:
    if not prices or not isfinite(monthly) or monthly <= 0:
        raise ValueError('prices and positive monthly contribution required')
    if strategy not in ('fixed', 'quarterly', 'dip', 'blend') or not 0 < threshold < 1 or lookback < 1 or cooldown < 0:
        raise ValueError('invalid strategy parameters')
    if any(not isfinite(p) or p <= 0 for _, p in prices) or any(prices[i][0] <= prices[i-1][0] for i in range(1, len(prices))):
        raise ValueError('prices must be positive and dates strictly increasing')
    shares = cash = invested = 0.0
    flows, equity, closes = [], [], []
    last_month, last_add = None, -cooldown
    for i, (day, price) in enumerate(prices):
        month = (day.year, day.month)
        new_month = month != last_month
        if new_month:
            cash += monthly
            invested += monthly
            flows.append((day, -monthly))
            last_month = month
        closes.append(price)
        drawdown = 1 - price / max(closes[-lookback:])
        spend = 0.0
        if strategy == 'fixed' and new_month:
            spend = cash
        elif strategy == 'quarterly' and new_month and day.month in (1, 4, 7, 10):
            spend = cash
        elif strategy == 'dip' and drawdown >= threshold and i-last_add >= cooldown:
            spend = cash * .5
            if spend: last_add = i
        elif strategy == 'blend':
            if new_month:
                spend = min(cash, monthly * 2 / 3)
            available = cash - spend
            if i-last_add >= cooldown and drawdown >= threshold and available > 0:
                extra = min(available, monthly * (2/3 if drawdown >= threshold * 1.75 else 1/3))
                spend += extra
                last_add = i
        shares += spend / price
        cash -= spend
        equity.append((day, shares * price + cash))
    value = equity[-1][1]
    flows.append((prices[-1][0], value))
    try:
        annualized = xirr(flows)
    except ValueError:
        annualized = None
    return BacktestResult(invested, shares, cash, value, value / invested - 1, annualized, tuple(equity))
