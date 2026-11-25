"""Explicit brokerage cost calibrations, scoped to an account, symbol and statement date."""
from datetime import date
import json
from math import isfinite

from apps.api.accounts import _account, _check_request, _persist, _replay, _stamp
from packages.core.market_time import et_today
from packages.core.portfolio import summarize_trades
from packages.providers.history import normalize_symbol


SETTING = 'realized_baseline_usd'


def _decode(row):
    _, account_id, symbol = row['key'].split(':')
    value = json.loads(row['value'])
    return {'account_id': int(account_id), 'symbol': symbol, 'as_of': value['as_of'],
            'realized_baseline_usd': value['realized_baseline_usd']}


def read_calibrations(con):
    """Read using the caller's ledger snapshot. A bare scalar lacks a date and owner."""
    rows = con.execute('SELECT key,value FROM settings WHERE key GLOB ? ORDER BY key', (SETTING + ':*',))
    return [_decode(row) for row in rows]


def get_cost_basis_calibrations():
    from apps.api.main import db
    with db() as con:
        con.execute('BEGIN')
        unscoped = con.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
        return {'calibrations': read_calibrations(con),
                'unscoped_baseline_usd': unscoped['value'] if unscoped else None}


def set_cost_basis_calibration(account_id, symbol, as_of, realized_baseline_usd,
                               expected_revision, idempotency_key, dry_run=True, actor='mcp'):
    """Set one brokerage statement baseline, or clear the pair with two nulls."""
    from apps.api.main import db
    _check_request(expected_revision, idempotency_key)
    symbol = normalize_symbol(symbol)
    value = None
    if realized_baseline_usd is None:
        if as_of is not None:
            raise ValueError('Clear a calibration by passing null for both as_of and realized_baseline_usd')
    else:
        if type(realized_baseline_usd) not in (int, float) or not isfinite(realized_baseline_usd):
            raise ValueError('realized_baseline_usd must be a finite USD amount')
        if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
            raise ValueError('as_of must be the statement date in YYYY-MM-DD form')
        if as_of > et_today().isoformat():
            raise ValueError('Statement date cannot be in the future')
        value = {'as_of': as_of, 'realized_baseline_usd': float(realized_baseline_usd)}

    identity = json.dumps(['set_cost_basis_calibration', account_id, symbol, value, expected_revision, actor],
                          sort_keys=True)
    key = f'{SETTING}:{account_id}:{symbol}'
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        prior = _replay(con, idempotency_key, identity)
        if prior:
            return prior
        account = _account(con, account_id, expected_revision)
        row = con.execute('SELECT key,value FROM settings WHERE key=?', (key,)).fetchone()
        before = _decode(row) if row else None
        after = None
        ledger_realized = None
        if value is not None:
            if account['kind'] != 'broker' or account['currency'] != 'USD':
                raise ValueError('A calibration requires a USD brokerage account')
            trades = con.execute('''SELECT * FROM trades WHERE account_id=? AND symbol=?
                                    AND deleted=0 AND trade_date<=? ORDER BY trade_date,id''',
                                 (account_id, symbol, as_of)).fetchall()
            if not trades:
                raise ValueError('No recorded trades for this account and symbol by the statement date')
            position, ledger_realized = summarize_trades(trades, 0)
            if position.shares <= 0:
                raise ValueError('A calibration requires an open position at the end of the statement date')
            after = {'account_id': account_id, 'symbol': symbol, **value}

        details = {'account_id': account_id, 'symbol': symbol, 'before': before, 'after': after,
                   'expected_revision': expected_revision, 'ledger_realized_as_of_usd': ledger_realized,
                   'as_of_basis': 'end_of_statement_day'}
        if dry_run:
            return {'preview': details}
        if value is None:
            con.execute('DELETE FROM settings WHERE key=?', (key,))
        else:
            con.execute('''INSERT INTO settings(key,value,revision) VALUES(?,?,?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value,revision=excluded.revision,
                               updated_at=CURRENT_TIMESTAMP''', (key, json.dumps(value), expected_revision + 1))
        _stamp(con, account_id)
        result = {'account_id': account_id, 'symbol': symbol, 'calibration': after,
                  'revision': expected_revision + 1}
        return _persist(con, actor, 'set_cost_basis_calibration', details, idempotency_key, identity, result)
