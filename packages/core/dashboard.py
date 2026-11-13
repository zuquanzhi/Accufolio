"""Holdings dashboard reconstructed from trades and unadjusted daily closes.

There is no cash ledger: buys add invested capital and sells remove it. The
series describes securities value, not account NAV or time-weighted returns.
Unknown prices produce gaps, never a partial total presented as a full value.
"""
from datetime import date, timedelta
from math import isfinite

from .portfolio import summarize_trades
from .metrics import xirr

MAX_CARRY_DAYS = 7


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _trades(rows, end):
    result = []
    excluded = 0
    for index, row in enumerate(rows):
        if row.get('deleted'):
            continue
        day = _day(row['trade_date'])
        if day > end:
            excluded += 1
            continue
        shares, price = float(row['shares']), float(row['price'])
        if not isfinite(shares) or not isfinite(price) or shares <= 0 or price <= 0:
            raise ValueError('Trade quantities and prices must be positive and finite')
        if row.get('side', 'buy') not in ('buy', 'sell'):
            raise ValueError('Invalid trade side')
        trade_id = row.get('id', index)
        order = trade_id if isinstance(trade_id, (int, float)) else index
        result.append({**row, 'symbol': row['symbol'].upper(), 'trade_date': day.isoformat(),
                       'side': row.get('side', 'buy'), 'shares': shares, 'price': price, '_order': order})
    result.sort(key=lambda row: (row['trade_date'], row['_order']))
    return result, excluded


def summarize_account(trades, quotes, end):
    """Return known accounting values even when some market quotes are missing."""
    end = _day(end)
    rows, excluded = _trades(trades, end)
    grouped = {}
    monthly = {}
    buy_total = sell_total = realized_total = 0.0
    for row in rows:
        grouped.setdefault(row['symbol'], []).append(row)
        cash = row['shares'] * row['price']
        month = monthly.setdefault(row['trade_date'][:7], {'buy': 0.0, 'sell': 0.0})
        month[row['side']] += cash
        if row['side'] == 'buy':
            buy_total += cash
        else:
            sell_total += cash
    holdings, missing = [], []
    for symbol, history in sorted(grouped.items()):
        quote = quotes.get(symbol) or {}
        price = quote.get('price')
        if not isinstance(price, (int, float)) or not isfinite(price) or price <= 0:
            price = None
        position, realized = summarize_trades(history, price or 0)
        realized_total += realized
        if not position.shares:
            continue
        if price is None:
            missing.append(symbol)
        holdings.append({'symbol': symbol, 'shares': position.shares, 'cost': position.cost,
                         'average_price': position.average_price,
                         'value': position.value if price is not None else None,
                         'pnl': position.pnl if price is not None else None,
                         'realized_pnl': realized, 'price': price,
                         'change_percent': quote.get('change_percent'),
                         'source': quote.get('source')})
    cost = sum(row['cost'] for row in holdings)
    total_shares = sum(row['shares'] for row in holdings)
    value = None if missing else sum(row['value'] for row in holdings)
    net = buy_total - sell_total
    annualized = None
    annualized_reason = '行情不完整' if missing else '记录不足 90 天或没有可计算的现金流'
    if rows and value is not None:
        flows = [(_day(row['trade_date']), (-1 if row['side'] == 'buy' else 1) * row['shares'] * row['price']) for row in rows]
        if value > 0:
            flows.append((end, value))
        try:
            annualized = xirr(flows)
            if annualized is not None:
                annualized_reason = None
        except ValueError:
            annualized_reason = '现金流没有唯一可用的年化解'
    for row in holdings:
        row['value_weight'] = row['value'] / value if value else None
        row['cost_weight'] = row['cost'] / cost if cost else None
        row['shares_weight'] = row['shares'] / total_shares if total_shares else None
    return {
        'summary': {'value': value, 'cost': cost, 'net_invested': net,
                    'buy_total': buy_total, 'sell_total': sell_total,
                    'realized_pnl': realized_total,
                    'unrealized_pnl': value - cost if value is not None else None,
                    'total_pnl': value - net if value is not None else None,
                    'holding_count': len(holdings), 'trade_count': len(rows),
                    'shares': total_shares, 'xirr': annualized, 'xirr_reason': annualized_reason,
                    'excluded_future_trades': excluded},
        'holdings': holdings, 'quote_missing_symbols': missing,
        'contributions': [{'month': month, **cash, 'net': cash['buy'] - cash['sell']}
                          for month, cash in sorted(monthly.items())],
    }


def reconstruct(trades, histories, start, end):
    """Calendar-day positions valued at known closes, at most seven days old.

    Trade-date buys/sells are applied before that day's closing valuation.
    All earlier trades still contribute to holdings and net invested at start.
    A weekend trade is valued at the last available close, never a future bar.
    """
    start, end = _day(start), _day(end)
    if start > end or (end - start).days > 30 * 366:
        raise ValueError('Invalid dashboard date range')
    rows, _ = _trades(trades, end)
    if not rows:
        return {'series': [], 'history_missing_symbols': [], 'missing_days': 0}
    start = max(start, _day(rows[0]['trade_date']))
    symbols = sorted({row['symbol'] for row in rows})
    bars = {}
    for symbol in symbols:
        source = histories.get(symbol, [])
        source = source.get('bars', []) if isinstance(source, dict) else source
        normalized, previous = [], None
        for bar in source:
            day, close = _day(bar['day']), float(bar['close'])
            if not isfinite(close) or close <= 0 or (previous is not None and day <= previous):
                raise ValueError('Invalid historical prices')
            normalized.append((day, close))
            previous = day
        bars[symbol] = normalized
    positions = {symbol: 0.0 for symbol in symbols}
    cursors = {symbol: 0 for symbol in symbols}
    prices = {}
    index = 0
    net = 0.0
    points, missing_symbols, missing_days = [], set(), 0
    day = start
    while day <= end:
        while index < len(rows) and rows[index]['trade_date'] <= day.isoformat():
            row = rows[index]
            sign = 1 if row['side'] == 'buy' else -1
            positions[row['symbol']] += sign * row['shares']
            if positions[row['symbol']] < -1e-9:
                raise ValueError('Insufficient shares for sell transaction')
            if abs(positions[row['symbol']]) < 1e-9:
                positions[row['symbol']] = 0.0
            net += sign * row['shares'] * row['price']
            index += 1
        missing, value = [], 0.0
        for symbol in symbols:
            source = bars[symbol]
            cursor = cursors[symbol]
            while cursor < len(source) and source[cursor][0] <= day:
                prices[symbol] = source[cursor]
                cursor += 1
            cursors[symbol] = cursor
            quantity = positions[symbol]
            if not quantity:
                continue
            known = prices.get(symbol)
            if not known or (day - known[0]).days > MAX_CARRY_DAYS:
                missing.append(symbol)
            else:
                value += quantity * known[1]
        if missing:
            missing_days += 1
            missing_symbols.update(missing)
        points.append({'day': day.isoformat(), 'value': None if missing else value,
                       'net_invested': net, 'pnl': None if missing else value - net,
                       'missing_symbols': missing})
        day += timedelta(days=1)
    return {'series': points, 'history_missing_symbols': sorted(missing_symbols), 'missing_days': missing_days}


def build_dashboard(trades, histories, quotes, start, end, include_history=True):
    account = summarize_account(trades, quotes, end)
    history = reconstruct(trades, histories, start, end) if include_history else {
        'series': [], 'history_missing_symbols': [], 'missing_days': 0}
    quote_missing = account.pop('quote_missing_symbols')
    history_missing = history.pop('history_missing_symbols')
    missing_days = history.pop('missing_days')
    return {'as_of': _day(end).isoformat(), 'start': history['series'][0]['day'] if history['series'] else _day(start).isoformat(),
            'end': _day(end).isoformat(), 'history_loaded': include_history, **account, **history,
            'coverage': {'complete': not (quote_missing or history_missing),
                         'quote_missing_symbols': quote_missing, 'history_missing_symbols': history_missing,
                         'missing_symbols': sorted(set(quote_missing + history_missing)),
                         'missing_days': missing_days, 'stale_symbols': []}}
