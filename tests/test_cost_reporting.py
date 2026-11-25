"""Cross-check the public reports against the same calibrated, isolated journal."""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.accounts import Account, CashEntry, add_cash_entry, save_account
from apps.api.main import Trade, app, mutate_trade
from packages.mcp.server import set_cost_basis_calibration
from packages.providers import Quote, QuoteResult


class CostReportingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.dict(os.environ, {
            'ACCUFOLIO_DB': str(Path(tmp.name) / 'basis.db'), 'ACCUFOLIO_PASSWORD': 'test-cost',
            'ACCUFOLIO_USER': 'cost-reader', 'ACCUFOLIO_MCP_SCOPE': 'write'}))
        self.http = TestClient(app)
        self.http.auth = ('cost-reader', 'test-cost')
        self.addCleanup(self.http.close)

    def test_both_endpoints_agree_with_agent_calibration(self):
        mutate_trade('add', Trade(symbol='EXM', shares=2, price=68.70, fee=.40,
                                  trade_date='2025-03-07'), None, 'fixture')
        set_cost_basis_calibration(1, 'EXM', '2025-03-07', -7.15, 1, 'example-cal', dry_run=False)
        quote = QuoteResult(Quote('EXM', 67, 0, 'fixture', 0), False, ())
        with patch('apps.api.dashboard.get_quote', return_value=quote), patch('apps.api.main.get_quote', return_value=quote):
            summary = self.http.get('/api/summary', params={'symbol': 'exm'}).json()
            report = self.http.get('/api/dashboard', params={'include_history': False}).json()
        expected = dict(net_cost=137.80, realized_pnl=0, realized_baseline=-7.15, diluted_cost=130.65,
                        breakeven_price=68.90, average_price=65.325, pnl=3.35, unrealized_pnl=3.35, net_pnl=-3.8)
        holding = report['holdings'][0]
        for row in (summary, summary['position'], report['summary'], holding, holding['accounts'][0]):
            for field, value in expected.items():
                self.assertAlmostEqual(row[field], value, msg=field)
        self.assertAlmostEqual(summary['position']['return_rate'], 3.35 / 130.65)
        self.assertIn('2.4.0', summary['method_note'])

    def test_foreign_cash_residue_serializes_to_positive_zero(self):
        account = save_account(Account(name='Cash test', kind='bank', purpose='savings', currency='HKD'),
                               None, 0, 'account', dry_run=False)
        for index, amount in enumerate([.3, -.1, -.2]):
            entry = CashEntry(account_id=account['id'], currency='HKD', entry_date='2025-03-07',
                              amount=amount, kind='deposit' if amount > 0 else 'withdraw')
            result = add_cash_entry(entry, index, 'cash-' + str(index), dry_run=False)
        with patch('apps.api.fx.get_fx', return_value={'base':'USD', 'rates':{'USD':1, 'HKD':7.8}}):
            report = self.http.get('/api/assets').json()
        bank = next(row for row in report['accounts'] if row['id'] == account['id'])
        for value in (bank['balance'], bank['cash_value_usd'], bank['value_usd'], report['net_assets_usd'],
                      result['account']['balance']):
            self.assertEqual(value, 0.0)
            self.assertEqual(math.copysign(1, value), 1.0)
