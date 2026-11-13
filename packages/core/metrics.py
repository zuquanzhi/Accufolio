"""Pure return and drawdown calculations. Rates are fractions, not percentages."""
from datetime import date
from math import isfinite, log, log1p, exp, fsum

def twr(start_price: float, end_price: float) -> float:
    if not all(map(isfinite, (start_price, end_price))) or start_price <= 0 or end_price < 0:
        raise ValueError("prices must be finite; start must be positive and end non-negative")
    return end_price / start_price - 1

def max_drawdown(prices: list[tuple[date, float]]) -> tuple[float, date]:
    if not prices:
        raise ValueError("price series is empty")
    peak = 0.0
    worst = 0.0
    worst_date = prices[0][0]
    previous = None
    for day, price in prices:
        if previous is not None and day <= previous:
            raise ValueError("dates must be strictly increasing")
        if not isfinite(price) or price <= 0:
            raise ValueError("prices must be finite and positive")
        peak = max(peak, price)
        drawdown = price / peak - 1
        if drawdown < worst:
            worst, worst_date = drawdown, day
        previous = day
    return worst, worst_date

def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """XIRR for purchases followed by a final valuation; None if sample < 90 days."""
    if len(cashflows) < 2:
        raise ValueError("at least one purchase and a valuation are required")
    flows = sorted(cashflows, key=lambda item: item[0])
    if any(not isfinite(amount) for _, amount in flows):
        raise ValueError("cash flows must be finite")
    if any(amount >= 0 for _, amount in flows[:-1]) or flows[-1][1] <= 0:
        raise ValueError("expected negative purchases followed by a positive valuation")
    start = flows[0][0]
    if (flows[-1][0] - start).days < 90:
        return None
    def npv(rate):
        weights = [(log(abs(amount)) - ((day-start).days/365)*log1p(rate), 1 if amount > 0 else -1) for day,amount in flows]
        scale = max(weight for weight,_ in weights)
        return fsum(sign*exp(weight-scale) for weight,sign in weights)
    low, high = -0.9999, 10.0
    if npv(low) < 0 or npv(high) > 0:
        raise ValueError("XIRR root is outside the supported interval")
    for _ in range(200):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2
