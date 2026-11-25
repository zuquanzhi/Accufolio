import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from apps.api.accounts import Account, save_account
from apps.api.cost_basis import get_cost_basis_calibrations, read_calibrations, set_cost_basis_calibration
from apps.api.main import db, mutate_trade, Trade
from apps.api.extended import export
from apps.api.preferences import get_settings
from packages.mcp.server import set_cost_basis_calibration as mcp_set


class CostCalibrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(directory.name) / 'ledger.db')})
        env.start()
        self.addCleanup(env.stop)
        self.buy('ALFA')
        self.trade('ALFA', '2025-04-03', 1, 12, fee=1, side='sell')

    def trade(self, symbol, day, shares, price, fee=0, side='buy', account=1):
        return mutate_trade('add', Trade(account_id=account, symbol=symbol, trade_date=day,
                                         shares=shares, price=price, fee=fee, side=side), None, 'test')

    def buy(self, symbol, account=1):
        self.trade(symbol, '2025-04-02', 2, 10, account=account)

    def revision(self, account=1):
        with db() as con:
            return con.execute('SELECT revision FROM accounts WHERE id=?', (account,)).fetchone()[0]

    def test_preview_commit_retry_audit_clear_and_export(self):
        args = (1, 'alfa', '2025-04-03', -3.25, self.revision(), 'set-calibration')
        with patch.dict(os.environ, {'ACCUFOLIO_MCP_SCOPE': 'read'}), self.assertRaises(PermissionError):
            mcp_set(*args)
        with patch.dict(os.environ, {'ACCUFOLIO_MCP_SCOPE': 'write'}):
            before = mcp_set(*args)['preview']
            self.assertIsNone(before['before'])
            self.assertEqual(before['ledger_realized_as_of_usd'], 1)
            self.assertEqual(get_cost_basis_calibrations()['calibrations'], [])
            result = mcp_set(*args, dry_run=False)
            self.assertEqual(result, mcp_set(*args, dry_run=False))
        self.assertEqual(self.revision(), args[4] + 1)
        self.assertEqual(get_cost_basis_calibrations()['calibrations'], [before['after']])
        with db() as con:
            entry = con.execute('SELECT action,details FROM audit WHERE id=?', (result['audit_id'],)).fetchone()
        self.assertEqual(entry['action'], 'set_cost_basis_calibration')
        self.assertEqual(json.loads(entry['details']), before)
        stored_settings = json.loads(export(actor='test').body)['settings']
        calibrated = next(r for r in stored_settings if r['key'] == 'realized_baseline_usd:1:ALFA')
        self.assertEqual(json.loads(calibrated['value'])['as_of'], '2025-04-03')
        self.assertEqual(get_settings()['revision'], 0)
        old_revision = self.revision()
        clear_args = (1, 'ALFA', None, None, old_revision, 'clear-calibration')
        self.assertEqual(set_cost_basis_calibration(*clear_args)['preview']['before'], result['calibration'])
        set_cost_basis_calibration(*clear_args, dry_run=False)
        self.assertEqual(get_cost_basis_calibrations()['calibrations'], [])
        self.assertEqual(self.revision(), old_revision + 1)

    def test_trade_and_calibration_changes_invalidate_preview(self):
        args = (1, 'ALFA', '2025-04-03', -3, self.revision(), 'stale-preview')
        set_cost_basis_calibration(*args)
        self.buy('BETA')
        with self.assertRaises(HTTPException) as conflict:
            set_cost_basis_calibration(*args, dry_run=False)
        self.assertEqual(conflict.exception.status_code, 409)
        current = self.revision()
        set_cost_basis_calibration(1, 'ALFA', '2025-04-03', 5, current, 'first', False)
        for key, revision in [('second', current), ('first', self.revision())]:
            with self.subTest(key=key), self.assertRaises(HTTPException) as conflict:
                set_cost_basis_calibration(1, 'ALFA', '2025-04-03', 0, revision, key, False)
            self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(get_cost_basis_calibrations()['calibrations'][0]['realized_baseline_usd'], 5)

    def test_one_current_calibration_per_account_and_symbol_preserves_bare_setting(self):
        other = save_account(Account(name='Second', currency='USD', kind='broker', purpose='investing'),
                             None, 0, 'new-broker', False)['id']
        self.buy('ALFA', other)
        self.buy('BETA')
        with db() as con:
            con.execute("INSERT INTO settings(key,value) VALUES('realized_baseline_usd','8.25')")
            self.assertEqual(read_calibrations(con), [])
        for account, symbol, amount in [(1, 'ALFA', 2), (other, 'ALFA', -5), (1, 'BETA', 0), (1, 'ALFA', 3)]:
            set_cost_basis_calibration(account, symbol, '2025-04-02', amount, self.revision(account),
                                       f'set-{account}-{symbol}-{amount}', False)
        read = get_cost_basis_calibrations()
        self.assertEqual(read['unscoped_baseline_usd'], '8.25')
        self.assertEqual({(row['account_id'], row['symbol']): row['realized_baseline_usd']
                          for row in read['calibrations']}, {(1, 'ALFA'): 3, (1, 'BETA'): 0, (other, 'ALFA'): -5})

    def test_rejects_unknown_or_invalid_statement_without_writing(self):
        bank = save_account(Account(name='Checking', currency='USD', kind='bank', purpose='savings'),
                            None, 0, 'new-bank', False)['id']
        cases = [(1, 'ALFA', '2025-04-01', 1), (1, 'ALFA', '9999-12-31', 0),
                 (1, 'ALFA', None, 1), (1, 'ALFA', '2025-04-03', None),
                 (1, 'ALFA', '2025-04-03', float('nan')), (1, 'ALFA', '2025-04-03', True),
                 (1, 'UNKNOWN', '2025-04-03', 0), (bank, 'ALFA', '2025-04-03', 0)]
        for account, symbol, day, amount in cases:
            with self.subTest(account=account, day=day, amount=amount), self.assertRaises(ValueError):
                set_cost_basis_calibration(account, symbol, day, amount, self.revision(account), 'invalid', False)
        self.assertEqual(get_cost_basis_calibrations()['calibrations'], [])

    def test_statement_uses_end_of_day_position_and_clearing_after_full_exit(self):
        self.trade('ALFA', '2025-04-04', 1, 12, side='sell')
        args = (1, 'ALFA', '2025-04-04', 3.5)
        with self.assertRaisesRegex(ValueError, 'open position'):
            set_cost_basis_calibration(*args, self.revision(), 'closed', False)
        self.trade('ALFA', '2025-04-04', 1, 11)
        preview = set_cost_basis_calibration(*args, self.revision(), 'reopened')['preview']
        self.assertEqual(preview['ledger_realized_as_of_usd'], 3)
        self.assertEqual(preview['as_of_basis'], 'end_of_statement_day')
        set_cost_basis_calibration(*args, self.revision(), 'reopened', False)
        self.trade('ALFA', '2025-04-07', 1, 10, side='sell')
        set_cost_basis_calibration(1, 'ALFA', None, None, self.revision(), 'clear-closed', False)
        self.assertEqual(get_cost_basis_calibrations()['calibrations'], [])
