"""Holdings dashboard reconstructed from trades and unadjusted daily closes.

Cash entries are valued separately on the assets page. Buys add invested
capital and sells remove it here, including fees. This series describes
securities value, not total net assets or time-weighted returns.
Unknown prices produce gaps, never a partial total presented as a full value.
"""
from datetime import date
from math import isfinite

from .portfolio import summarize_trades
from .metrics import xirr
from .market_time import session_days


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
        fee = float(row.get('fee', 0))
        if not isfinite(fee) or fee < 0:
            raise ValueError('Trade fee must be finite and non-negative')
        if row.get('currency', 'USD') != 'USD':
            raise ValueError('Current securities providers support USD quotes only')
        if not isfinite(shares) or not isfinite(price) or shares <= 0 or price <= 0:
            raise ValueError('Trade quantities and prices must be positive and finite')
        if row.get('side', 'buy') not in ('buy', 'sell'):
            raise ValueError('Invalid trade side')
        trade_id = row.get('id', index)
        order = trade_id if isinstance(trade_id, (int, float)) else index
        result.append({**row, 'symbol': row['symbol'].upper(), 'trade_date': day.isoformat(),
                       'side': row.get('side', 'buy'), 'shares': shares, 'price': price, '_order': order})
        result[-1].update(fee=fee, currency='USD', account_id=row.get('account_id', 1))
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
        grouped.setdefault((row['account_id'], row['symbol']), []).append(row)
        cash = row['shares'] * row['price'] + (row['fee'] if row['side'] == 'buy' else -row['fee'])
        month = monthly.setdefault(row['trade_date'][:7], {'buy': 0.0, 'sell': 0.0})
        month[row['side']] += cash
        if row['side'] == 'buy':
            buy_total += cash
        else:
            sell_total += cash
    holdings, missing = [], []
    account_holdings = []
    realized_by_symbol = {}
    for (account_id, symbol), history in sorted(grouped.items()):
        quote = quotes.get(symbol) or {}
        price = quote.get('price')
        if not isinstance(price, (int, float)) or not isfinite(price) or price <= 0:
            price = None
        position, realized = summarize_trades(history, price or 0)
        realized_total += realized
        realized_by_symbol[symbol] = realized_by_symbol.get(symbol, 0) + realized
        if not position.shares:
            continue
        if price is None:
            missing.append(symbol)
        account_holdings.append({'symbol': symbol, 'account_id': account_id, 'shares': position.shares, 'cost': position.cost,
                         'average_price': position.average_price,
                         'value': position.value if price is not None else None,
                         'pnl': position.pnl if price is not None else None,
                         'realized_pnl': realized, 'price': price,
                         'change_percent': quote.get('change_percent'),
                         'source': quote.get('source'), 'quote_timestamp': quote.get('timestamp')})
    # Cost basis is computed inside each account before the same security is
    # combined; a sale in one account must never consume another's shares.
    for symbol in sorted({h['symbol'] for h in account_holdings}):
        parts = [h for h in account_holdings if h['symbol'] == symbol]
        row = {key: value for key, value in parts[0].items() if key != 'account_id'}
        for key in ('shares', 'cost', 'realized_pnl'):
            row[key] = sum(h[key] for h in parts)
        row['realized_pnl'] = realized_by_symbol[symbol]
        for key in ('value', 'pnl'):
            row[key] = None if any(h[key] is None for h in parts) else sum(h[key] for h in parts)
        row['average_price'] = row['cost']/row['shares']
        row['accounts'] = parts
        holdings.append(row)
    cost = sum(row['cost'] for row in holdings)
    total_shares = sum(row['shares'] for row in holdings)
    value = None if missing else sum(row['value'] for row in holdings)
    net = buy_total - sell_total
    annualized = None
    annualized_reason = '行情不完整' if missing else '记录不足 90 天或没有可计算的现金流'
    if rows and value is not None:
        flows = [(_day(row['trade_date']), (-1 if row['side'] == 'buy' else 1) * row['shares'] * row['price'] - row['fee']) for row in rows]
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
                    'fees': sum(row['fee'] for row in rows),
                    'excluded_future_trades': excluded},
        'holdings': holdings, 'quote_missing_symbols': sorted(set(missing)),
        'contributions': [{'month': month, **cash, 'net': cash['buy'] - cash['sell']}
                          for month, cash in sorted(monthly.items())],
    }


def reconstruct(trades, histories, start, end):
    """Regular trading-session positions valued only at that session's close.

    Trade-date buys/sells are applied before that day's closing valuation.
    All earlier trades still contribute to holdings and net invested at start.
    Weekend trades enter the next trading-session position. Missing closes
    remain gaps; prior-session prices never masquerade as today's close.
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
    account_positions = {}
    cursors = {symbol: 0 for symbol in symbols}
    prices = {}
    index = 0
    net = 0.0
    points, missing_symbols, missing_days = [], set(), 0
    for day in session_days(start, end):
        while index < len(rows) and rows[index]['trade_date'] <= day.isoformat():
            row = rows[index]
            sign = 1 if row['side'] == 'buy' else -1
            key = (row['account_id'], row['symbol'])
            account_positions[key] = account_positions.get(key, 0) + sign * row['shares']
            if account_positions[key] < -1e-9:
                raise ValueError('Insufficient shares in the trade account')
            positions[row['symbol']] += sign * row['shares']
            if positions[row['symbol']] < -1e-9:
                raise ValueError('Insufficient shares for sell transaction')
            if abs(positions[row['symbol']]) < 1e-9:
                positions[row['symbol']] = 0.0
            net += sign * row['shares'] * row['price'] + row['fee']
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
            if not known or known[0] != day:
                missing.append(symbol)
            else:
                value += quantity * known[1]
        if missing:
            missing_days += 1
            missing_symbols.update(missing)
        points.append({'day': day.isoformat(), 'value': None if missing else value,
                       'net_invested': net, 'pnl': None if missing else value - net,
                       'missing_symbols': missing})
    # Never extend the chart's endpoint with an old close or an invented "today".
    # Missing-session coverage still includes the omitted, unavailable tail.
    while points and points[-1]['value'] is None:
        points.pop()
    return {'series': points, 'history_missing_symbols': sorted(missing_symbols), 'missing_days': missing_days}


def build_dashboard(trades, histories, quotes, start, end, include_history=True, history_end=None):
    account = summarize_account(trades, quotes, end)
    cutoff = _day(history_end) if history_end is not None else _day(end)
    history = reconstruct(trades, histories, start, cutoff) if include_history and _day(start) <= cutoff else {
        'series': [], 'history_missing_symbols': [], 'missing_days': 0}
    quote_missing = account.pop('quote_missing_symbols')
    history_missing = history.pop('history_missing_symbols')
    missing_days = history.pop('missing_days')
    return {'as_of': _day(end).isoformat(), 'start': history['series'][0]['day'] if history['series'] else _day(start).isoformat(),
            'end': history['series'][-1]['day'] if history['series'] else None,
            'history_loaded': include_history, **account, **history,
            'coverage': {'complete': not (quote_missing or history_missing),
                         'quote_missing_symbols': quote_missing, 'history_missing_symbols': history_missing,
                         'missing_symbols': sorted(set(quote_missing + history_missing)),
                         'missing_days': missing_days, 'stale_symbols': []}}
