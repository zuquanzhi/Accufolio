"""Pure return and drawdown calculations. Rates are fractions, not percentages."""
from datetime import date
from math import isfinite, log, log1p, exp, expm1, fsum

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
    """Annual money-weighted return for dated buys, sales and terminal value.

    Uses actual elapsed days / 365. Same-day cash flows are netted and zero
    flows ignored, so a fully liquidated position ends on its last cash flow.
    Samples shorter than 90 days return None. The supported rate interval is
    [-99.99%, 1000%]; no root, multiple roots, or an ill-conditioned repeated
    root raise ValueError instead of selecting an arbitrary initial guess.
    """
    if len(cashflows) < 2:
        raise ValueError("at least two dated cash flows are required")
    grouped = {}
    for day, amount in cashflows:
        if not isinstance(day, date) or not isfinite(amount):
            raise ValueError("cash flows must have valid dates and finite amounts")
        day = date.fromordinal(day.toordinal())
        grouped.setdefault(day, []).append(amount)
    try:
        flows = [(day, fsum(amounts)) for day, amounts in sorted(grouped.items())]
    except OverflowError as exc:
        raise ValueError("cash flow total exceeds the supported range") from exc
    flows = [(day, amount) for day, amount in flows if amount]
    if not any(amount < 0 for _, amount in flows) or not any(amount > 0 for _, amount in flows):
        raise ValueError("both positive and negative net cash flows are required")
    start = flows[0][0]
    if (flows[-1][0] - start).days < 90:
        return None
    # In x=log(1+r), NPV is a sum of exponentials. Log coefficients and
    # rescaling at evaluation prevent overflow for long spans / extreme cash.
    terms = [((day-start).days / 365, log(abs(amount)), 1 if amount > 0 else -1)
             for day, amount in flows]
    low, high = log1p(-.9999), log1p(10.0)

    def evaluate(polynomial, value):
        weights = [(coefficient - elapsed * value, sign) for elapsed, coefficient, sign in polynomial]
        scale = max(weight for weight, _ in weights)
        return fsum(sign * exp(weight-scale) for weight, sign in weights)

    def bisect(polynomial, left, right):
        before = evaluate(polynomial, left)
        for _ in range(100):
            middle = (left + right) / 2
            if middle == left or middle == right:
                break
            current = evaluate(polynomial, middle)
            if current == 0:
                return middle
            if (before < 0) == (current < 0):
                left, before = middle, current
            else:
                right = middle
        return (left + right) / 2

    # Differentiate exp(pivot*x)*NPV, then discard its positive exponential
    # factor. Choosing a pivot at the first coefficient-sign transition removes
    # at least one sign variation. The bottom polynomial has at most one root;
    # its roots delimit monotone intervals of its parent. Work upwards to find
    # every root, including close roots that a fixed sampling grid would miss.
    levels = [terms]
    stored_terms = len(terms)
    while sum(a[2] != b[2] for a, b in zip(levels[-1], levels[-1][1:])) > 1:
        current = levels[-1]
        pivot = next(b[0] for a, b in zip(current, current[1:]) if a[2] != b[2])
        derivative = [(elapsed, coefficient + log(abs(pivot-elapsed)), sign * (1 if pivot > elapsed else -1))
                      for elapsed, coefficient, sign in current if elapsed != pivot]
        stored_terms += len(derivative)
        if stored_terms > 100_000:
            raise ValueError("cash flow pattern is too complex to establish a unique XIRR")
        levels.append(derivative)

    roots = []
    for polynomial in reversed(levels):
        boundaries = [low, *[root for root in roots if low < root < high], high]
        values = [evaluate(polynomial, point) for point in boundaries]
        # Near-zero at a stationary point cannot distinguish a double root
        # from an extremely close pair at floating-point precision.
        if polynomial is terms and any(abs(value) <= 1e-11 for value in values[1:-1]):
            raise ValueError("XIRR is not uniquely resolvable near a repeated root")
        roots = [point for point, value in zip(boundaries, values) if abs(value) <= 1e-13]
        for left, right, before, after in zip(boundaries, boundaries[1:], values, values[1:]):
            if abs(before) <= 1e-13 or abs(after) <= 1e-13:
                continue
            if (before < 0) != (after < 0):
                roots.append(bisect(polynomial, left, right))
        roots.sort()
    if not roots:
        raise ValueError("XIRR root is outside the supported interval")
    if len(roots) != 1:
        raise ValueError("cash flows have multiple XIRR roots in the supported interval")
    return expm1(roots[0])
