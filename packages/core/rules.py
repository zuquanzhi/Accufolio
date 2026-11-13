"""Market signal predicates with explicit missing-data handling."""
from math import isfinite

def calm_signal(price: float, closes: list[float], vix: float, vix_peak: float) -> bool:
    values = [price, vix, vix_peak, *closes]
    if not closes or any(not isfinite(v) or v <= 0 for v in values):
        raise ValueError('positive finite price, history and volatility values required')
    peak = max(vix_peak, vix)
    return vix / peak <= .75 and price / max(closes[-20:]) <= .92

def movement_level(change_percent: float, threshold: float = 2.0) -> int:
    """Return 0 below the threshold, then L1/L2/L3 at 1x/2x/3x."""
    if not isfinite(change_percent) or not isfinite(threshold) or threshold <= 0:
        raise ValueError('finite change and positive threshold required')
    for level in (3, 2, 1):
        if abs(change_percent) >= threshold * level:
            return level
    return 0
