"""Windowed account valuations and cash-flow-adjusted performance.

Values use completed US trading-session closes, actual dated cash entries and
same-day/prior-published FX supplied by the caller. Linked trade settlements
create a receivable/payable between execution and cash settlement. External
inflows are assumed at the start of each valuation interval and outflows at its
end; returns are a daily approximation, not a claim of GIPS compliance.
"""
from datetime import date
from math import fsum, isfinite

from .dashboard import _trades
from .market_time import session_days


def _day(value):
    return value if type(value) is date else date.fromisoformat(value)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def build_net_worth(accounts, trades, cash_entries, settlements, histories, fx_points,
                    start, end, display_currency='USD'):
    """Return original-account cash, securities, unsettled cash, NAV and drawdown.

    The first valuation in the selected window is the performance baseline.
    Historical records before it determine balances and holdings, but do not
    require old prices or FX. An unavailable price/FX or unclassified trade
    settlement prevents a whole-window return from being presented as complete.
    All input accounts, including archived accounts, belong to the same scope.
    ``fx_points`` rates mean 1 USD = rate units of the named currency.
    """
    start, end = _day(start), _day(end)
    if start > end or (end-start).days > 30*366:
        raise ValueError('Invalid net assets date range')
    if display_currency not in {'USD', 'CNY', 'HKD', 'EUR'}:
        raise ValueError('Unsupported display currency')
    account_map = {a['id']: a for a in accounts}
    if len(account_map) != len(accounts):
        raise ValueError('Duplicate accounts')
    rows, _ = _trades(trades, end)
    trade_map = {t['id']: t for t in rows}
    if len(trade_map) != len(rows) or any(not isfinite(t['shares']*t['price']+t['fee']) for t in rows):
        raise ValueError('Invalid or duplicate trade')
    cash_map = {}
    for entry in cash_entries:
        if entry['id'] in cash_map or not _finite(entry['amount']):
            raise ValueError('Invalid or duplicate cash entry')
        if entry['account_id'] not in account_map:
            raise ValueError('Cash entry account is missing')
        if entry['currency'] != account_map[entry['account_id']]['currency']:
            raise ValueError('Cash entry currency does not match its account')
        _day(entry['entry_date'])
        cash_map[entry['id']] = dict(entry)
    if any(t['account_id'] not in account_map for t in rows):
        raise ValueError('Trade account is missing')
    cash_rows = sorted((e for e in cash_map.values() if _day(e['entry_date']) <= end),
                       key=lambda e: (e['entry_date'], e['id']))
    issues, issue_keys = [], set()
    def issue(code, message, day=None):
        key = (code, message, day)
        if key not in issue_keys:
            issues.append({'code': code, 'message': message, 'day': day})
            issue_keys.add(key)

    # Explicit links are classification evidence. Never infer one from amount
    # or date alone: the same amount may also be salary or an external withdrawal.
    reversed_ids = {e['reverses_entry_id'] for e in cash_rows if e.get('reverses_entry_id') is not None}
    links, used_cash = {}, set()
    invalid_links = set()
    for link in settlements:
        tid, cid = link['trade_id'], link['cash_entry_id']
        trade, cash = trade_map.get(tid), cash_map.get(cid)
        if trade is None:
            continue  # A future trade is outside this valuation cutoff.
        expected = (-(trade['shares']*trade['price']+trade['fee']) if trade['side'] == 'buy'
                    else trade['shares']*trade['price']-trade['fee'])
        valid = (cash is not None and tid not in links and cid not in used_cash
                 and cash['account_id'] == trade['account_id'] and cash['currency'] == trade['currency'] == 'USD'
                 and cash['kind'] == ('withdraw' if expected < 0 else 'deposit')
                 and not cash.get('transfer_group') and cash.get('counter_account_id') is None
                 and cash.get('symbol') in (None,trade['symbol']) and cash.get('reverses_entry_id') is None
                 and cid not in reversed_ids and cash['entry_date'] >= trade['trade_date']
                 and abs(cash['amount']-expected) <= .005+1e-9)
        if not valid:
            invalid_links.add(tid)
            issue('invalid_settlement', f'交易 #{tid} 的结算关联不完整或与成交不符。')
        else:
            links[tid] = cash
            used_cash.add(cid)
    for tid in invalid_links:
        removed = links.pop(tid, None)
        if removed:
            used_cash.discard(removed['id'])
    unmatched = [t['id'] for t in rows if t['id'] not in links and
                 abs((-(t['shares']*t['price']+t['fee']) if t['side'] == 'buy'
                      else t['shares']*t['price']-t['fee'])) > 1e-9]
    if unmatched:
        issue('unmatched_settlement', '证券交易尚有未核对的现金结算，暂不计算资金调整收益。')

    rates = {p['day']: p.get('rates', {}) for p in fx_points}
    missing_fx, missing_fx_days, missing_symbols, missing_quote_days = set(), set(), set(), set()
    def rate(currency, day):
        if currency == 'USD':
            return 1.0
        result = rates.get(day, {}).get(currency)
        if not _finite(result) or result <= 0:
            missing_fx.add(currency); missing_fx_days.add(day)
            return None
        return result
    def convert(amount, currency, day):
        if not _finite(amount):
            issue('invalid_amount', '资产金额超出可计算范围。', day)
            return None
        if amount == 0:
            return 0.0
        # Same-currency cash needs no cross rate, even when the optional USD
        # reference is absent; returns must use the selected currency itself.
        if currency == display_currency:
            return amount
        source, target = rate(currency, day), rate(display_currency, day)
        result = None if source is None or target is None else amount/source*target
        if result is not None and not _finite(result):
            issue('invalid_amount', '折算金额超出可计算范围。', day)
            return None
        return result
    def add(values):
        if any(v is None or not _finite(v) for v in values):
            return None
        try:
            return fsum(values)
        except OverflowError:
            issue('invalid_amount', '资产合计超出可计算范围。')
            return None

    prices = {}
    for symbol, history in histories.items():
        bars = history.get('bars', []) if isinstance(history, dict) else history
        previous = None; by_day = {}
        for bar in bars:
            day, close = _day(bar['day']), bar['close']
            if not _finite(close) or close <= 0 or previous is not None and day <= previous:
                raise ValueError('Invalid historical prices')
            previous = day; by_day[day.isoformat()] = close
        prices[symbol.upper()] = by_day

    external_kinds = {'balance_open', 'deposit', 'withdraw'}
    internal_kinds = {'interest', 'dividend', 'fee', 'transfer_in', 'transfer_out', 'fx_in', 'fx_out'}
    unknown_flow = False
    transfer_kinds = {'transfer_in', 'transfer_out', 'fx_in', 'fx_out'}
    transfer_groups = {}
    for entry in cash_rows:
        if entry['kind'] in transfer_kinds:
            transfer_groups.setdefault(entry.get('transfer_group'), []).append(entry)
    for group, legs in transfer_groups.items():
        valid = bool(group) and len(legs) == 2
        if valid:
            a, b = legs
            expected_kinds = {'transfer_in', 'transfer_out'} if a['currency'] == b['currency'] else {'fx_in', 'fx_out'}
            valid = (a['account_id'] != b['account_id'] and a.get('counter_account_id') == b['account_id']
                     and b.get('counter_account_id') == a['account_id'] and a['entry_date'] == b['entry_date']
                     and {a['kind'], b['kind']} == expected_kinds
                     and a['amount']*b['amount'] < 0
                     and all((leg['amount'] > 0) == leg['kind'].endswith('_in') for leg in legs)
                     and (a['currency'] != b['currency'] or abs(a['amount']+b['amount']) <= 1e-8))
        if not valid:
            unknown_flow = True
            issue('unpaired_transfer', '账户转账缺少可核对的成对流水，暂不计算资金调整收益。')
    def external(entry):
        nonlocal unknown_flow
        origin = entry
        if entry.get('reverses_entry_id') is not None:
            origin = cash_map.get(entry['reverses_entry_id'])
            if not origin or origin.get('reverses_entry_id') is not None:
                unknown_flow = True
                issue('unclassified_reversal', f'流水 #{entry["id"]} 缺少可核对的冲销原记录。')
                return False
        if origin['id'] in used_cash:
            return False
        kind = origin['kind']
        if kind in external_kinds:
            return True
        if kind in internal_kinds:
            return False
        unknown_flow = True
        issue('unclassified_cash', f'流水 #{entry["id"]} 的资金性质无法识别。')
        return False

    first_days = [t['trade_date'] for t in rows] + [e['entry_date'] for e in cash_rows]
    days = session_days(max(start, _day(min(first_days))), end) if first_days else []
    balances = {account_id: 0.0 for account_id in account_map}
    corrections = {account_id: 0.0 for account_id in account_map}
    positions, trade_index, cash_index = {}, 0, 0
    series = []
    for day in days:
        iso = day.isoformat()
        while trade_index < len(rows) and rows[trade_index]['trade_date'] <= iso:
            trade = rows[trade_index]; key = (trade['account_id'], trade['symbol'])
            positions[key] = positions.get(key, 0.0) + (1 if trade['side'] == 'buy' else -1)*trade['shares']
            if positions[key] < -1e-9:
                raise ValueError('Insufficient shares in the trade account')
            if abs(positions[key]) < 1e-9:
                positions[key] = 0.0
            trade_index += 1
        inflows, outflows = [], []
        while cash_index < len(cash_rows) and cash_rows[cash_index]['entry_date'] <= iso:
            cash = cash_rows[cash_index]; account_id = cash['account_id']
            delta = cash['amount']-corrections[account_id]
            total = balances[account_id]+delta
            if not isfinite(total):
                raise ValueError('Cash balance exceeds the supported range')
            corrections[account_id] = (total-balances[account_id])-delta
            balances[account_id] = total
            if total < -1e-8:
                raise ValueError('Cash entry creates a negative historical balance')
            # The first snapshot already contains all earlier cash. Its flows
            # must not be subtracted a second time from window performance.
            if series and external(cash):
                flow = convert(cash['amount'], cash['currency'], cash['entry_date'])
                (inflows if cash['amount'] > 0 else outflows).append(None if flow is None else abs(flow))
            cash_index += 1
        cash_value = add([convert(amount, account_map[account_id]['currency'], iso)
                          for account_id, amount in balances.items()])
        security_usd = 0.0; missing = []
        for (_, symbol), quantity in positions.items():
            if quantity <= 0:
                continue
            price = prices.get(symbol, {}).get(iso)
            if price is None:
                missing.append(symbol); missing_symbols.add(symbol); missing_quote_days.add(iso)
            else:
                security_usd += quantity*price
        securities = None if missing else convert(security_usd, 'USD', iso)
        pending_usd = fsum(cash['amount'] for tid, cash in links.items()
                           if trade_map[tid]['trade_date'] <= iso < cash['entry_date'])
        unsettled = convert(pending_usd, 'USD', iso)
        incoming, outgoing = add(inflows), add(outflows)
        flow = None if incoming is None or outgoing is None else incoming-outgoing
        series.append({'day': iso, 'value': add([cash_value, securities, unsettled]),
                       'cash': cash_value, 'securities': securities, 'unsettled': unsettled,
                       'external_flow': flow, 'inflow': incoming, 'outflow': outgoing,
                       'nav': None, 'drawdown': None})

    if missing_symbols:
        issue('missing_prices', '部分估值日缺少同日收盘价，相关资产值保持空缺。')
    if missing_fx:
        issue('missing_fx', '估值或资金流缺少当日可用汇率，相关金额保持空缺。')
    if not series:
        issue('no_data', '当前区间尚无可用的账户估值。')
    classified = not unmatched and not invalid_links and not unknown_flow
    values_complete = bool(series) and all(p['value'] is not None and p['value'] >= 0 for p in series)
    flow_complete = all(p['external_flow'] is not None for p in series)
    continuous = classified
    nav = peak = None
    worst, worst_day = 0.0, series[0]['day'] if series else None
    for index, point in enumerate(series):
        if not classified:
            point['external_flow'] = point['inflow'] = point['outflow'] = None
        if not continuous or point['value'] is None or point['value'] < 0 or point['external_flow'] is None:
            continuous = False
            continue
        if index == 0:
            nav = peak = 1.0
        else:
            previous = series[index-1]
            denominator = previous['value']+point['inflow']
            numerator = point['value']+point['outflow']
            if denominator == 0 and numerator == 0:
                factor = 1.0  # No capital or return during this empty interval.
            elif denominator <= 0 or numerator < 0:
                continuous = False
                issue('zero_capital', '区间没有可计算的投入资本，无法连续计算净值。', point['day'])
                continue
            else:
                factor = numerator/denominator
            nav *= factor
            if not isfinite(nav):
                continuous = False
                issue('invalid_nav', '资金调整净值超出可计算范围。', point['day'])
                continue
            peak = max(peak, nav)
        point['nav'] = nav
        point['drawdown'] = nav/peak-1
        if point['drawdown'] < worst:
            worst, worst_day = point['drawdown'], point['day']
    capital_observed = any((p['value'] is not None and p['value'] > 0) or
                           (p['inflow'] is not None and p['inflow'] > 0) or
                           (p['outflow'] is not None and p['outflow'] > 0) for p in series)
    if series and len(series) < 2:
        issue('insufficient_history', '至少需要两个收盘估值才能比较区间收益。')
    if series and not capital_observed:
        issue('no_capital', '当前区间没有已投入的资产，不计算收益率。')
    performance_complete = len(series) > 1 and continuous and values_complete and flow_complete and capital_observed
    start_value, end_value = (series[0]['value'], series[-1]['value']) if series else (None, None)
    net_flow = fsum(p['external_flow'] for p in series[1:]) if classified and flow_complete and series else None
    gain = end_value-start_value-net_flow if (start_value is not None and end_value is not None
                                             and net_flow is not None and values_complete) else None
    summary = {'start_day': series[0]['day'] if series else None, 'end_day': series[-1]['day'] if series else None,
               'start_value': start_value, 'end_value': end_value, 'net_external_flow': net_flow, 'gain': gain,
               'twr': nav-1 if performance_complete and len(series) > 1 else None,
               'max_drawdown': worst if performance_complete and len(series) > 1 else None,
               'drawdown_date': worst_day if performance_complete and len(series) > 1 else None}
    return {'currency': display_currency, 'series': series, 'summary': summary,
            'method': 'daily_cashflow_adjusted',
            'flow_timing': '期初入金、期末出金；首个收盘估值为区间基准。',
            'coverage': {'complete': performance_complete, 'valuation_complete': values_complete and classified,
                         'prices_complete': values_complete,
                         'performance_complete': performance_complete, 'unmatched_trade_ids': sorted(unmatched),
                         'missing_symbols': sorted(missing_symbols), 'missing_quote_days': sorted(missing_quote_days),
                         'missing_fx': sorted(missing_fx), 'missing_fx_days': sorted(missing_fx_days),
                         'issues': issues},
            'warnings': [item['message'] for item in issues]}
