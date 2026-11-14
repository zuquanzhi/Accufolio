"""Attribute net cash dividends without adding the same cash to securities value.

Journal reversals take effect on their recorded date. Unattributable receipts
block total-return calculations while their journal balance is nonzero.
"""
from datetime import date
from math import fsum, isfinite


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def dividends_for(trades, cash_entries, end, symbol=None):
    end = end.isoformat() if isinstance(end, date) else end
    first_buys = {}
    for trade in trades:
        if trade.get('deleted') or trade.get('side', 'buy') != 'buy':
            continue
        key = (trade.get('account_id', 1), trade['symbol'].upper())
        day = str(trade['trade_date'])
        first_buys[key] = min(day, first_buys.get(key, day))
    rows = list(cash_entries)
    reversals = {}
    for row in rows:
        if row.get('reverses_entry_id') is not None:
            reversals.setdefault(row['reverses_entry_id'], []).append(row)
    events, issues, intervals = [], [], []
    for original in rows:
        if original.get('kind') != 'dividend' or original.get('reverses_entry_id') is not None:
            continue
        ticker = (original.get('symbol') or '').upper()
        if symbol is not None and ticker and ticker != symbol:
            continue
        original_day = str(original['entry_date'])
        if original_day > end:
            continue
        key = (original['account_id'], ticker)
        group = [original, *[r for r in reversals.get(original.get('id'), []) if str(r['entry_date']) <= end]]
        group.sort(key=lambda row: (str(row['entry_date']), row.get('id', 0)))
        reason = None
        if not ticker:
            reason = '分红尚未填写标的'
        elif original['currency'] != 'USD':
            reason = '非 USD 分红尚不能计入美元证券收益'
        elif key not in first_buys or first_buys[key] > original_day:
            reason = '分红账户与标的缺少此前的买入记录'
        valid_amounts = all(_number(r['amount']) for r in group)
        valid_reversals = len(group) <= 2 and all(r['account_id'] == original['account_id'] and
                              r['currency'] == original['currency'] and
                              str(r['entry_date']) >= original_day and r['amount'] < 0 and
                              abs(r['amount'] + original['amount']) <= 1e-8
                              for r in group if r is not original) if valid_amounts else False
        structurally_valid = valid_amounts and valid_reversals and _number(original['amount']) and original['amount'] > 0
        if not structurally_valid:
            reason = '分红或冲销流水不完整，请核对原始记录'
        if reason:
            amount = fsum(r['amount'] for r in group) if valid_amounts else None
            # A correction restores coverage from its own date; earlier chart
            # points remain unknown rather than silently rewriting history.
            remaining = 0.0
            changes = {}
            for row in group:
                changes.setdefault(str(row['entry_date']), []).append(row['amount'])
            active_since = None
            for day, values in sorted(changes.items()):
                remaining = fsum([remaining, *values]) if structurally_valid else None
                active = remaining is None or abs(remaining) > 1e-8
                if active and active_since is None:
                    active_since = day
                if not active and active_since is not None:
                    intervals.append({'start': active_since, 'end': day})
                    active_since = None
            if active_since is not None:
                intervals.append({'start': active_since, 'end': None})
                issues.append({'entry_id': original.get('id'), 'account_id': original['account_id'],
                               'symbol': ticker or None, 'currency': original['currency'],
                               'amount': amount, 'entry_date': original_day, 'reason': reason})
            continue
        for row in group:
            events.append({'entry_id': row.get('id'), 'account_id': original['account_id'],
                           'symbol': ticker, 'currency': 'USD', 'day': str(row['entry_date']),
                           'amount': row['amount'], 'reverses_entry_id': row.get('reverses_entry_id')})
    events.sort(key=lambda event: (event['day'], event['entry_id'] or 0))
    return {'events': events, 'issues': issues, 'issue_intervals': intervals,
            'cash_dividends': fsum(event['amount'] for event in events), 'complete': not issues}


def covered_on(dividends, day):
    return not any(interval['start'] <= day and (interval['end'] is None or day < interval['end'])
                   for interval in dividends['issue_intervals'])
