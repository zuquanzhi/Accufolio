"""Historical FX is dated, durable, and never filled with future/current rates."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api import fx
from packages.providers.fx import ecb_history, HISTORY_URL, MAX_HISTORY_BYTES

SNAPSHOTS = [
    {'base':'USD','as_of':'1999-01-04','rates':{'USD':1.,'EUR':.8,'HKD':7.75}},
    {'base':'USD','as_of':'2025-01-03','rates':{'USD':1.,'EUR':.9,'HKD':7.7,'CNY':7.}},
    {'base':'USD','as_of':'2025-01-06','rates':{'USD':1.,'EUR':.8,'HKD':7.8,'CNY':8.}},
    {'base':'USD','as_of':'2025-01-07','rates':{'USD':1.,'EUR':.7,'HKD':7.9,'CNY':9.}},
]
XML = b'''<Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube>
<Cube time="2025-01-06"><Cube currency="USD" rate="1.25"/><Cube currency="HKD" rate="9.75"/><Cube currency="CNY" rate="10"/></Cube>
<Cube time="1999-01-04"><Cube currency="USD" rate="1.25"/><Cube currency="HKD" rate="9.6875"/></Cube>
</Cube></Envelope>'''


class HistoryProviderTests(unittest.TestCase):
    def test_archive_sorted_cross_rates_and_early_missing_currency(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return XML
        rows = ecb_history(fetch)
        self.assertEqual(urls, [HISTORY_URL])
        self.assertEqual(rows, [SNAPSHOTS[0], SNAPSHOTS[2]])
        self.assertNotIn('CNY', rows[0]['rates'])

    def test_malformed_oversized_unsafe_and_future_archives_rejected(self):
        for raw in [b'{}', b'<html/>', b' '*(MAX_HISTORY_BYTES+1),
                    b'<!DOCTYPE document [<!ENTITY x "bad">]>'+XML,
                    XML.replace(b'2025-01-06', b'1999-01-04'), XML.replace(b'2025-01-06', b'2099-01-01'),
                    XML.replace(b'2025-01-06', b'1998-01-01'), XML.replace(b'9.75', b'NaN'),
                    XML.replace(b'9.75', b'0'), XML.replace(b'currency="USD"', b'currency="GBP"'),
                    XML.replace(b'currency="CNY"', b'currency="HKD"')]:
            with self.subTest(raw=raw[:80]), self.assertRaises((ValueError, ElementTree.ParseError)):
                ecb_history(lambda _:raw)


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB':str(self.path),'ACCUFOLIO_PASSWORD':'fx-history'})
        self.env.start()
        self.now = time.time()
        self.clock = patch('apps.api.fx.time.time', side_effect=lambda:self.now)
        self.clock.start()
        self.provider = patch('apps.api.fx.ecb_history', return_value=SNAPSHOTS)
        self.fetch = self.provider.start()
        app = FastAPI()
        app.include_router(fx.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.provider.stop()
        self.clock.stop()
        self.env.stop()
        self.temp.cleanup()

    def points(self, days, **options):
        return {point['day']:point for point in fx.get_historical_fx(days, **options)['points']}

    def test_weekend_uses_previous_publication_never_future_or_latest(self):
        points = self.points(['2025-01-02','2025-01-03','2025-01-04','2025-01-06','2025-01-16'])
        self.assertIsNone(points['2025-01-02']['rates']['CNY'])
        self.assertEqual(points['2025-01-03']['rates']['CNY'],7)
        self.assertEqual(points['2025-01-04']['rates']['CNY'],7)
        self.assertEqual(points['2025-01-04']['rate_dates']['CNY'],'2025-01-03')
        self.assertEqual(points['2025-01-06']['rates']['CNY'],8)
        self.assertIsNone(points['2025-01-16']['rates']['CNY'])
        self.assertEqual(points['2025-01-06']['source'],'ECB')
        self.assertIsNotNone(points['2025-01-06']['fetched_ats']['CNY'])
        self.fetch.assert_called_once_with()

    def test_currency_before_coverage_stays_missing(self):
        points = self.points(['1998-12-31','1999-01-04'])
        self.assertEqual(points['1998-12-31']['rates']['USD'],1)
        self.assertIsNone(points['1998-12-31']['rates']['HKD'])
        self.assertIsNone(points['1999-01-04']['rates']['CNY'])
        self.assertEqual(points['1999-01-04']['rates']['HKD'],7.75)

    def test_cross_currency_direction_and_same_publication_date(self):
        point = self.points(['2025-01-06'],base='HKD')['2025-01-06']
        self.assertAlmostEqual(point['rates']['CNY'],8/7.8)
        self.assertAlmostEqual(point['rates']['USD'],1/7.8)
        self.assertEqual(point['rates']['HKD'],1)
        self.assertEqual(point['rate_dates']['CNY'],'2025-01-06')
        with fx._cache_db() as con:
            con.execute("DELETE FROM fx_rates WHERE quote='HKD' AND rate_date='2025-01-06'")
        point = self.points(['2025-01-06'],base='HKD')['2025-01-06']
        self.assertAlmostEqual(point['rates']['CNY'],7/7.7)
        self.assertEqual(point['rate_dates']['CNY'],'2025-01-03')

    def test_persisted_snapshots_and_negative_cache_recovery(self):
        first = self.points(['2025-01-06'])
        self.now += fx.TTL_SECONDS+1
        self.fetch.side_effect = TimeoutError('private provider URL')
        stale = fx.get_historical_fx(['2025-01-06'])
        self.assertTrue(stale['stale'])
        self.assertEqual(first['2025-01-06']['rates'],stale['points'][0]['rates'])
        self.assertNotIn('private',str(stale))
        fx.get_historical_fx(['2025-01-06'])
        self.assertEqual(self.fetch.call_count,2)
        self.now += fx.RETRY_SECONDS+1
        self.fetch.side_effect = None
        self.assertFalse(fx.get_historical_fx(['2025-01-06'])['stale'])
        self.assertEqual(self.fetch.call_count,3)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM fx_rates').fetchone()[0],11)

    def test_cold_failure_identity_only_and_no_network_retry_until_cooldown(self):
        self.fetch.side_effect = OSError('offline')
        result = fx.get_historical_fx(['2025-01-06'])
        self.assertEqual(result['points'][0]['rates'],{'USD':1,'CNY':None,'HKD':None,'EUR':None})
        fx.get_historical_fx(['2025-01-06'])
        self.fetch.assert_called_once_with()

    def test_daily_rate_snapshot_not_used_for_earlier_dates(self):
        with patch('apps.api.fx.ecb_rates', return_value=SNAPSHOTS[-1]):
            fx.get_fx()
        self.fetch.side_effect = OSError('offline archive')
        point = self.points(['2025-01-06'])['2025-01-06']
        self.assertIsNone(point['rates']['CNY'])

    def test_network_fetch_outside_sqlite_transaction_and_coalesced(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            with closing(sqlite3.connect(self.path,timeout=.1)) as con:
                con.execute('BEGIN IMMEDIATE')
                con.execute('CREATE TABLE independent_write(id INTEGER)')
                con.commit()
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test timeout')
            return SNAPSHOTS
        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = [pool.submit(fx.get_historical_fx,['2025-01-06']) for _ in range(3)]
            self.assertTrue(entered.wait(3))
            release.set()
            for future in pending:
                self.assertEqual(future.result(timeout=4)['points'][0]['rates']['CNY'],8)
        self.fetch.assert_called_once_with()

    def test_api_requires_auth_valid_dates_and_known_currency(self):
        path='/api/fx/history?start=2025-01-03&end=2025-01-06'
        self.assertEqual(self.client.get(path).status_code,401)
        self.fetch.assert_not_called()
        response=self.client.get(path,auth=('admin','fx-history'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['points']),4)
        for query in ['start=2025-01-06&end=2025-01-03', 'start=1900-01-01&end=2025-01-01',
                      'start=2025-01-03&end=2025-01-06&base=BAD','start=2099-01-01&end=2099-01-02']:
            self.assertEqual(self.client.get('/api/fx/history?'+query,auth=('admin','fx-history')).status_code,422)


if __name__=='__main__':
    unittest.main()
