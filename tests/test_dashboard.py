import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.dashboard import router
from apps.api.main import db
from packages.core.dashboard import build_dashboard, reconstruct, summarize_account
from packages.providers import Quote, QuoteResult


TRADES = [
    {'id': 1, 'symbol': 'AAA', 'trade_date': '2025-01-03', 'shares': 2, 'price': 100, 'side': 'buy'},
    {'id': 2, 'symbol': 'BBB', 'trade_date': '2025-01-04', 'shares': 1, 'price': 50, 'side': 'buy'},
    {'id': 3, 'symbol': 'AAA', 'trade_date': '2025-01-06', 'shares': 1, 'price': 120, 'side': 'sell'},
]
HISTORIES = {
    'AAA': [{'day': '2025-01-03', 'close': 110}, {'day': '2025-01-06', 'close': 125}, {'day': '2025-01-07', 'close': 130}],
    'BBB': [{'day': '2025-01-03', 'close': 50}, {'day': '2025-01-06', 'close': 55}, {'day': '2025-01-07', 'close': 60}],
}
QUOTES = {'AAA': {'price': 130, 'change_percent': 4, 'source': 'test'}, 'BBB': {'price': 60, 'change_percent': 9, 'source': 'test'}}


class DashboardTests(unittest.TestCase):
    def test_actual_trades_multiple_symbols_cost_profit_and_allocations(self):
        result = build_dashboard(TRADES, HISTORIES, QUOTES, '2025-01-01', '2025-01-07')
        summary = result['summary']
        expected = {'value': 190, 'cost': 150, 'net_invested': 130, 'buy_total': 250,
                    'sell_total': 120, 'realized_pnl': 20, 'unrealized_pnl': 40, 'total_pnl': 60,
                    'shares': 2, 'holding_count': 2, 'trade_count': 3}
        for key, value in expected.items():
            self.assertAlmostEqual(summary[key], value, msg=key)
        aaa, bbb = result['holdings']
        self.assertAlmostEqual(aaa['value_weight'], 130 / 190)
        self.assertAlmostEqual(aaa['cost_weight'], 100 / 150)
        self.assertAlmostEqual(aaa['shares_weight'], .5)
        self.assertAlmostEqual(bbb['average_price'], 50)
        self.assertEqual(result['contributions'], [{'month': '2025-01', 'buy': 250, 'sell': 120, 'net': 130}])
        self.assertTrue(result['coverage']['complete'])

    def test_weekend_trade_enters_next_session_without_inventing_weekend_points(self):
        rows = reconstruct(TRADES, HISTORIES, '2025-01-03', '2025-01-07')['series']
        self.assertEqual([r['day'] for r in rows], ['2025-01-03', '2025-01-06', '2025-01-07'])
        self.assertEqual([r['value'] for r in rows], [220, 180, 190])
        self.assertEqual([r['net_invested'] for r in rows], [200, 130, 130])
        self.assertEqual([r['pnl'] for r in rows], [20, 50, 60])

    def test_window_keeps_previous_trades_and_omits_future(self):
        future = {'symbol': 'CCC', 'trade_date': '2026-01-01', 'shares': 3, 'price': 10, 'side': 'buy'}
        result = build_dashboard(TRADES + [future], HISTORIES, QUOTES, '2025-01-06', '2025-01-07')
        self.assertEqual(len(result['series']), 2)
        self.assertEqual(result['series'][0]['net_invested'], 130)
        self.assertEqual(result['summary']['trade_count'], 3)
        self.assertEqual(result['summary']['excluded_future_trades'], 1)

    def test_missing_quote_never_shows_partial_total_or_allocation(self):
        result = summarize_account(TRADES, {'AAA': QUOTES['AAA']}, '2025-01-07')
        self.assertIsNone(result['summary']['value'])
        self.assertIsNone(result['summary']['total_pnl'])
        self.assertIsNone(result['summary']['unrealized_pnl'])
        self.assertEqual(result['summary']['cost'], 150)
        self.assertEqual(result['summary']['realized_pnl'], 20)
        self.assertTrue(all(h['value_weight'] is None for h in result['holdings']))
        self.assertEqual(result['holdings'][0]['shares_weight'], .5)

    def test_missing_history_is_gap_not_invented_price_or_partial_total(self):
        history = {**HISTORIES, 'BBB': HISTORIES['BBB'][2:]}
        result = build_dashboard(TRADES, history, QUOTES, '2025-01-03', '2025-01-07')
        self.assertEqual([r['value'] for r in result['series']], [220, None, 190])
        self.assertEqual(result['coverage']['history_missing_symbols'], ['BBB'])
        self.assertEqual(result['coverage']['missing_days'], 1)
        self.assertFalse(result['coverage']['complete'])

    def test_old_close_never_extends_series_to_a_later_day(self):
        result = reconstruct(TRADES[:1], {'AAA': HISTORIES['AAA'][:1]}, '2025-01-03', '2025-01-12')
        self.assertEqual(len(result['series']), 1)
        self.assertEqual(result['series'][0]['day'], '2025-01-03')
        self.assertEqual(result['series'][0]['value'], 220)
        self.assertGreater(result['missing_days'], 0)

    def test_closed_position_has_zero_value_without_quote_and_keeps_profit(self):
        sell = {'symbol': 'AAA', 'trade_date': '2025-01-06', 'shares': 2, 'price': 120, 'side': 'sell'}
        result = build_dashboard([TRADES[0], sell], {}, {}, '2025-01-06', '2025-01-07')
        self.assertEqual(result['summary']['value'], 0)
        self.assertEqual(result['summary']['realized_pnl'], 40)
        self.assertEqual(result['summary']['net_invested'], -40)
        self.assertEqual(result['series'][0]['value'], 0)
        self.assertEqual(result['series'][0]['pnl'], 40)
        self.assertEqual(result['holdings'], [])
        self.assertTrue(result['coverage']['complete'])

    def test_symbol_realized_includes_a_closed_account_position(self):
        rows = [dict(TRADES[0],account_id=1),
                dict(TRADES[0],id=2,account_id=2),
                {'id':3,'account_id':1,'symbol':'AAA','trade_date':'2025-01-06',
                 'shares':2,'price':120,'side':'sell','fee':2}]
        result=summarize_account(rows,QUOTES,'2025-01-07')
        self.assertEqual(result['summary']['realized_pnl'],38)
        self.assertEqual(result['holdings'][0]['realized_pnl'],38)
        self.assertEqual(result['holdings'][0]['shares'],2)

    def test_account_xirr_uses_sales_and_open_terminal_value(self):
        trades = [{'symbol': 'AAA', 'trade_date': '2025-01-01', 'shares': 10, 'price': 10, 'side': 'buy'},
                  {'symbol': 'AAA', 'trade_date': '2026-01-01', 'shares': 5, 'price': 11, 'side': 'sell'}]
        result = summarize_account(trades, {'AAA': {'price': 11}}, '2026-01-01')
        self.assertAlmostEqual(result['summary']['xirr'], .1)
        trades[-1]['shares'] = 10
        result = summarize_account(trades, {}, '2026-06-01')
        self.assertAlmostEqual(result['summary']['xirr'], .1)

    def test_empty_deleted_and_invalid_records(self):
        result = build_dashboard([{**TRADES[0], 'deleted': 1}], {}, {}, '2025-01-01', '2025-01-07')
        self.assertEqual(result['series'], [])
        self.assertEqual(result['summary']['value'], 0)
        with self.assertRaises(ValueError):
            build_dashboard([{**TRADES[0], 'side': 'sell'}], HISTORIES, QUOTES, '2025-01-01', '2025-01-07')
        with self.assertRaises(ValueError):
            reconstruct(TRADES, {'AAA': [{'day': '2025-01-03', 'close': float('nan')}]}, '2025-01-01', '2025-01-07')



class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(self.temp.name) / 'dashboard.db'),
                                          'ACCUFOLIO_PASSWORD': 'test-password', 'ACCUFOLIO_WEBHOOK_URL': ''})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.auth = ('admin', 'test-password')

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def insert(self):
        with db() as con:
            for row in TRADES:
                con.execute('INSERT INTO trades(symbol,trade_date,shares,price,side) VALUES(?,?,?,?,?)',
                            tuple(row[k] for k in ('symbol', 'trade_date', 'shares', 'price', 'side')))

    def test_auth_period_and_empty_dashboard(self):
        self.assertEqual(self.client.get('/api/dashboard').status_code, 401)
        self.assertEqual(self.client.get('/api/dashboard?period=no', auth=self.auth).status_code, 422)
        with patch('apps.api.dashboard.get_quote') as quote, patch('apps.api.dashboard.get_history') as history:
            response = self.client.get('/api/dashboard', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['value'], 0)
        self.assertEqual(response.json()['series'], [])
        quote.assert_not_called()
        history.assert_not_called()

    def test_provider_calls_are_outside_database_and_partial_failure_visible(self):
        self.insert()
        def quote(symbol):
            with db() as con:
                con.execute("INSERT INTO audit(actor,action,details) VALUES('test','provider-network','{}')")
            return QuoteResult(Quote(symbol, QUOTES[symbol]['price'], 1, 'test', 0), False, ())
        def history(symbol, **kwargs):
            if symbol == 'BBB':
                raise RuntimeError('provider unavailable')
            return {'bars': HISTORIES[symbol], 'source': 'test', 'cached': True, 'stale': True,
                    'errors': [], 'warnings': ['未复权']}
        with patch('apps.api.dashboard.get_quote', side_effect=quote), patch('apps.api.dashboard.get_history', side_effect=history):
            response = self.client.get('/api/dashboard?period=all', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['summary']['value'], 190)
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['stale_symbols'], ['AAA'])
        self.assertIn('BBB：日线暂不可用', result['warnings'])
        self.assertEqual(result['method'], 'trade_reconstruction_unadjusted')

    def test_headline_does_not_fetch_history_and_one_failed_quote_is_isolated(self):
        self.insert()
        def quote(symbol):
            if symbol == 'BBB':
                raise RuntimeError('connection reset')
            return QuoteResult(Quote(symbol, 130, 1, 'test', 0), False, ())
        with patch('apps.api.dashboard.get_quote', side_effect=quote), patch('apps.api.dashboard.get_history') as history:
            response = self.client.get('/api/dashboard?include_history=false', auth=self.auth)
        history.assert_not_called()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result['history_loaded'])
        self.assertEqual(result['series'], [])
        self.assertIsNone(result['summary']['value'])
        self.assertEqual(result['summary']['cost'], 150)
        self.assertEqual(result['coverage']['quote_missing_symbols'], ['BBB'])
        self.assertEqual(result['coverage']['history_missing_symbols'], [])

    def test_closed_before_display_window_does_not_fetch_unneeded_sources(self):
        self.insert()
        with db() as con:
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,side) VALUES('AAA','2025-01-08',1,130,'sell')")
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,side) VALUES('BBB','2025-01-08',1,60,'sell')")
        with patch('apps.api.dashboard.get_quote') as quote, patch('apps.api.dashboard.get_history') as history:
            response = self.client.get('/api/dashboard?period=1m', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['value'], 0)
        quote.assert_not_called()
        history.assert_not_called()


if __name__ == '__main__':
    unittest.main()
