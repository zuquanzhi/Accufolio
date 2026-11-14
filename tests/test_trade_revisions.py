"""Account revisions must invalidate settlement previews after any trade change."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from apps.api.accounts import Account, CashEntry, add_cash_entry, save_account
from apps.api.main import Trade, db, mutate_trade
from apps.api.extended import undo
from apps.api.settlements import list_settlements, set_trade_settlement


class TradeRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'ACCUFOLIO_DB': str(Path(self.temp.name) / 'test.db'),
            'ACCUFOLIO_PASSWORD': 'test',
        })
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)
        self.sequence = 0

    def key(self):
        self.sequence += 1
        return f'trade-revision-{self.sequence}'

    def revisions(self):
        with db() as con:
            return {row['id']: row['revision'] for row in con.execute('SELECT id,revision FROM accounts')}

    def snapshot(self):
        with db() as con:
            return {table: [dict(row) for row in con.execute('SELECT * FROM ' + table)]
                    for table in ('accounts', 'trades', 'audit', 'requests', 'reversals')}

    def trade(self, **overrides):
        return Trade(**{'symbol': 'AAA', 'trade_date': '2025-01-03',
                        'shares': 1, 'price': 100, **overrides})

    def cash(self, amount, kind):
        return add_cash_entry(CashEntry(account_id=1, entry_date='2025-01-06',
                                       kind=kind, amount=amount, currency='USD'),
                              self.revisions()[1], self.key(), False)['entry']

    def broker(self, name):
        return save_account(Account(name=name, kind='broker', purpose='investing',
                                    currency='USD'), None, 0, self.key(), False)['id']

    def test_same_amount_trade_edit_invalidates_settlement_preview(self):
        self.cash(1000, 'balance_open')
        trade = mutate_trade('add', self.trade(), None, 'test')
        cash = self.cash(-100, 'withdraw')
        revision = self.revisions()[1]
        key = self.key()
        self.assertIn('preview', set_trade_settlement(trade['id'], cash['id'], revision, key))

        # Equal settlement amounts must not hide a change of security or quantity.
        mutate_trade('update', self.trade(symbol='BBB', shares=2, price=50), trade['id'], 'test')
        self.assertEqual(self.revisions()[1], revision + 1)
        with self.assertRaises(HTTPException) as stale:
            set_trade_settlement(trade['id'], cash['id'], revision, key, False)
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(list_settlements(actor='test'), [])
        # Re-reading the account allows a new, explicit confirmation to succeed.
        set_trade_settlement(trade['id'], cash['id'], self.revisions()[1], self.key(), False)
        self.assertEqual(list_settlements(actor='test')[0]['trade_id'], trade['id'])

    def test_add_update_delete_and_each_undo_stamp_once(self):
        revision = self.revisions()[1]
        added = mutate_trade('add', self.trade(), None, 'test')
        self.assertEqual(self.revisions()[1], revision + 1)
        updated = mutate_trade('update', self.trade(price=120), added['id'], 'test')
        self.assertEqual(self.revisions()[1], revision + 2)
        deleted = mutate_trade('delete', None, added['id'], 'test')
        self.assertEqual(self.revisions()[1], revision + 3)
        for offset, action in enumerate((deleted, updated, added), start=4):
            undo(action['audit_id'], actor='test')
            self.assertEqual(self.revisions()[1], revision + offset)
        before_retry = self.snapshot()
        with self.assertRaises(HTTPException) as repeated:
            undo(added['audit_id'], actor='test')
        self.assertEqual(repeated.exception.status_code, 409)
        self.assertEqual(self.snapshot(), before_retry)

    def test_moving_trade_and_undo_stamp_both_accounts_once(self):
        destination = self.broker('Destination')
        unrelated = self.broker('Unrelated')
        added = mutate_trade('add', self.trade(), None, 'test')
        before = self.revisions()
        moved = mutate_trade('update', self.trade(account_id=destination), added['id'], 'test')
        expected = {**before, 1: before[1] + 1, destination: before[destination] + 1}
        self.assertEqual(self.revisions(), expected)
        undo(moved['audit_id'], actor='test')
        self.assertEqual(self.revisions(), {**expected, 1: before[1] + 2,
                                           destination: before[destination] + 2})
        self.assertEqual(self.revisions()[unrelated], before[unrelated])

    def test_dry_runs_leave_revisions_and_journal_unchanged(self):
        destination = self.broker('Destination')
        added = mutate_trade('add', self.trade(), None, 'test')
        baseline = self.snapshot()
        operations = [('add', self.trade(symbol='BBB'), None),
                      ('update', self.trade(price=120), added['id']),
                      ('update', self.trade(account_id=destination), added['id']),
                      ('delete', None, added['id'])]
        for action, trade, trade_id in operations:
            with self.subTest(action=action, trade=trade):
                self.assertIn('preview', mutate_trade(action, trade, trade_id, 'test',
                                                     self.key(), dry_run=True))
                self.assertEqual(self.snapshot(), baseline)

    def test_idempotent_retries_do_not_stamp_twice(self):
        trade_id = None
        operations = [('add', self.trade()), ('update', self.trade(price=120)), ('delete', None)]
        for action, trade in operations:
            with self.subTest(action=action):
                before = self.revisions()[1]
                key = self.key()
                result = mutate_trade(action, trade, trade_id, 'test', key)
                after = self.snapshot()
                self.assertEqual(self.revisions()[1], before + 1)
                self.assertEqual(mutate_trade(action, trade, trade_id, 'test', key), result)
                self.assertEqual(self.snapshot(), after)
                trade_id = result['id']

    def test_invalid_history_rolls_back_trade_and_all_account_revisions(self):
        destination = self.broker('Destination')
        buy = mutate_trade('add', self.trade(shares=2), None, 'test')
        mutate_trade('add', self.trade(side='sell', trade_date='2025-01-06'), None, 'test')
        baseline = self.snapshot()
        operations = [lambda: mutate_trade('add', self.trade(side='sell', shares=2,
                                                            trade_date='2025-01-07'), None, 'test'),
                      lambda: mutate_trade('update', self.trade(shares=0.5), buy['id'], 'test'),
                      lambda: mutate_trade('update', self.trade(account_id=destination), buy['id'], 'test'),
                      lambda: mutate_trade('delete', None, buy['id'], 'test'),
                      lambda: undo(buy['audit_id'], actor='test')]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(HTTPException) as invalid:
                    operation()
                self.assertEqual(invalid.exception.status_code, 409)
                self.assertEqual(self.snapshot(), baseline)


if __name__ == '__main__':
    unittest.main()
