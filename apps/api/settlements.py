"""Explicit links between recorded securities trades and actual cash settlement."""
import json
import math

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize

router = APIRouter()


def ensure_schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS trade_settlements(
        trade_id INTEGER PRIMARY KEY REFERENCES trades(id),
        cash_entry_id INTEGER NOT NULL UNIQUE REFERENCES cash_entries(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def has_link(con, *, trade_id=None, cash_entry_id=None):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_settlements'").fetchone():
        return False
    field, value = ('trade_id', trade_id) if trade_id is not None else ('cash_entry_id', cash_entry_id)
    return con.execute('SELECT 1 FROM trade_settlements WHERE '+field+'=?', (value,)).fetchone() is not None


def require_unlinked(con, *, trade_id=None, cash_entry_id=None):
    if has_link(con, trade_id=trade_id, cash_entry_id=cash_entry_id):
        raise HTTPException(409, '先通过 set_trade_settlement 解除结算关联，再更正交易或冲销流水。')


@router.get('/api/settlements')
def list_settlements(actor: str = Depends(authorize)):
    from apps.api.main import db
    with db() as con:
        ensure_schema(con)
        return [dict(row) for row in con.execute('SELECT * FROM trade_settlements ORDER BY trade_id')]


def set_trade_settlement(trade_id, cash_entry_id, expected_revision, key, dry_run=True, actor='mcp'):
    """Link an existing actual cash entry, or unlink with null. No cash is invented."""
    from apps.api.main import db
    from apps.api.accounts import _check_request, _replay, _persist, _account, _stamp
    _check_request(expected_revision, key)
    if type(trade_id) is not int or not 0 < trade_id < 2**63:
        raise ValueError('trade_id must be a positive integer')
    if cash_entry_id is not None and (type(cash_entry_id) is not int or not 0 < cash_entry_id < 2**63):
        raise ValueError('cash_entry_id must be a positive integer or null')
    identity = json.dumps(['trade_settlement', trade_id, cash_entry_id, expected_revision, actor], sort_keys=True)
    with db() as con:
        ensure_schema(con)
        con.execute('BEGIN IMMEDIATE')
        prior = _replay(con, key, identity)
        if prior:
            return prior
        row = con.execute('SELECT * FROM trades WHERE id=? AND deleted=0', (trade_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Trade not found')
        trade = dict(row)
        account = _account(con, trade['account_id'], expected_revision)
        old = con.execute('SELECT * FROM trade_settlements WHERE trade_id=?', (trade_id,)).fetchone()
        entry = None
        if cash_entry_id is not None:
            row = con.execute('SELECT * FROM cash_entries WHERE id=?', (cash_entry_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Cash entry not found')
            entry = dict(row)
            expected = (-1 if trade['side'] == 'buy' else 1) * trade['shares'] * trade['price'] - trade['fee']
            if trade['currency'] != 'USD' or entry['currency'] != trade['currency'] or entry['account_id'] != trade['account_id']:
                raise ValueError('Settlement must use the same USD brokerage account as the trade')
            if expected == 0 or entry['kind'] != ('withdraw' if expected < 0 else 'deposit'):
                raise ValueError('Settlement direction must match the net amount after fees; zero cash needs no entry')
            if entry['transfer_group'] or entry['counter_account_id'] is not None or entry['reverses_entry_id'] is not None:
                raise ValueError('A transfer or reversal cannot settle a trade')
            if con.execute('SELECT 1 FROM cash_entries WHERE reverses_entry_id=?', (cash_entry_id,)).fetchone():
                raise ValueError('A reversed cash entry cannot settle a trade')
            if entry['entry_date'] < trade['trade_date']:
                raise ValueError('Cash settlement cannot predate the trade')
            if not math.isfinite(expected) or abs(entry['amount']-expected) > 0.005 + 1e-9:
                raise ValueError('Settlement must match the trade including fees, within half a USD cent')
            if entry['symbol'] is not None and entry['symbol'] != trade['symbol']:
                raise ValueError('Settlement symbol does not match the trade')
            if con.execute('SELECT 1 FROM trade_settlements WHERE cash_entry_id=? AND trade_id!=?', (cash_entry_id,trade_id)).fetchone():
                raise HTTPException(409, 'Cash entry is already linked to another trade')
        preview = {'trade_id': trade_id, 'cash_entry_id': cash_entry_id, 'account_id': account['id'],
                   'trade': trade, 'cash_entry': entry, 'before': dict(old) if old else None,
                   'expected_revision': expected_revision}
        if dry_run:
            return {'preview': preview}
        if cash_entry_id is None:
            con.execute('DELETE FROM trade_settlements WHERE trade_id=?', (trade_id,))
        else:
            con.execute('''INSERT INTO trade_settlements(trade_id,cash_entry_id) VALUES(?,?)
                ON CONFLICT(trade_id) DO UPDATE SET cash_entry_id=excluded.cash_entry_id,
                    created_at=CURRENT_TIMESTAMP''', (trade_id, cash_entry_id))
        _stamp(con, account['id'])
        result = {'trade_id': trade_id, 'cash_entry_id': cash_entry_id,
                  'account_id': account['id'], 'revision': expected_revision+1}
        return _persist(con, actor, 'set_trade_settlement', preview, key, identity, result)
