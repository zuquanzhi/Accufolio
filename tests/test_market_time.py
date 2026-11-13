from datetime import date, datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app, db
from packages.core.market_time import market_clock, session_days
from packages.providers import Quote, QuoteResult


class MarketClockTests(unittest.TestCase):
    def test_cst_next_day_is_still_previous_et_trading_day(self):
        result = market_clock(datetime.fromisoformat('2026-11-14T01:00:00+08:00'))
        self.assertEqual(result['date'], '2026-11-13')
        self.assertEqual(result['last_completed_session'], '2026-11-12')
        self.assertEqual(result['phase'], 'intraday')

    def test_dst_and_regular_close(self):
        for instant, expected in [('2026-06-16T19:59:00+00:00','2026-06-15'),
                                  ('2026-06-16T20:00:00+00:00','2026-06-16'),
                                  ('2026-11-13T20:59:00+00:00','2026-11-12'),
                                  ('2026-11-13T21:00:00+00:00','2026-11-13')]:
            self.assertEqual(market_clock(datetime.fromisoformat(instant))['last_completed_session'], expected)

    def test_holidays_weekends_and_early_close(self):
        cases = [('2026-11-27T12:59:00-05:00','2026-11-25'),
                 ('2026-11-27T13:00:00-05:00','2026-11-27'),
                 ('2026-11-28T18:00:00-05:00','2026-11-27'),
                 ('2026-07-03T18:00:00-04:00','2026-07-02'),
                 ('2026-04-03T18:00:00-04:00','2026-04-02')]
        for instant, expected in cases:
            self.assertEqual(market_clock(datetime.fromisoformat(instant))['last_completed_session'], expected)
        # The presidential mourning closure is not a generic weekday holiday.
        self.assertNotIn(date(2025,1,9), session_days(date(2025,1,6), date(2025,1,10)))


class DashboardDateRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(self.temp.name)/'db'),
                 'ACCUFOLIO_PASSWORD':'test-password','ACCUFOLIO_USER':'admin'})
        self.env.start()
        self.client = TestClient(app)
        self.auth = ('admin','test-password')
        with db() as con:
            con.execute("INSERT INTO trades(symbol,trade_date,shares,price,side) VALUES('QQQM','2026-11-01',3,270,'buy')")

    def tearDown(self):
        self.client.close();self.env.stop();self.temp.cleanup()

    def test_user_report_latest_quote_and_last_completed_close_have_distinct_dates(self):
        clock = market_clock(datetime.fromisoformat('2026-11-14T01:00:00+08:00'))
        daily = {'bars':[{'day':'2026-11-12','close':283.39},
                          {'day':'2026-11-13','close':280}, {'day':'2026-11-14','close':999}],
                 'source':'fixture','stale':False,'errors':[],'warnings':[]}
        with patch('apps.api.dashboard.market_clock', return_value=clock), \
             patch('apps.api.dashboard.get_history', return_value=daily) as history, \
             patch('apps.api.dashboard.get_quote', return_value=QuoteResult(Quote('QQQM',278.79,-1,'fixture',0),False,())):
            data = self.client.get('/api/dashboard',auth=self.auth).json()
        self.assertEqual(data['as_of'],'2026-11-13')
        self.assertEqual(data['end'],'2026-11-12')
        self.assertEqual(data['series'][-1]['day'],'2026-11-12')
        self.assertAlmostEqual(data['series'][-1]['value'],850.17)
        self.assertAlmostEqual(data['summary']['value'],836.37)
        self.assertEqual(data['history_basis'],'completed_session_close')
        self.assertEqual(data['valuation_basis'],'latest_quote')
        self.assertEqual(history.call_args.kwargs['end'],date(2026,11,12))

    def test_summary_requires_explicit_symbol_and_identifies_single_symbol_scope(self):
        self.assertEqual(self.client.get('/api/summary',auth=self.auth).status_code,422)
        with patch('apps.api.main.get_quote', return_value=QuoteResult(Quote('QQQM',280,1,'fixture',0),False,())):
            data=self.client.get('/api/summary?symbol=QQQM',auth=self.auth).json()
        self.assertEqual(data['scope'],'single_symbol')
        self.assertEqual(data['symbol'],'QQQM')
        self.assertEqual(data['currency'],'USD')


if __name__ == '__main__':
    unittest.main()
