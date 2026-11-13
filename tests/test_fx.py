"""Display conversion remains separate from bookkeeping and fails explicitly."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
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
from packages.providers.fx import ecb_rates, SOURCE_URL, MAX_RESPONSE_BYTES


DAY = datetime.now(timezone.utc).date().isoformat()
DATA = {'base': 'USD', 'rates': {'USD': 1.0, 'CNY': 7.2, 'HKD': 7.8, 'EUR': .8}, 'as_of': DAY}
XML = ('''<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time="''' + DAY + '''"><Cube currency="USD" rate="1.25"/><Cube currency="CNY" rate="9"/>
<Cube currency="HKD" rate="9.75"/><Cube currency="GBP" rate=".85"/></Cube></Cube></gesmes:Envelope>''').encode()


class FxProviderTests(unittest.TestCase):
    def test_ecb_shape_cross_rates_and_fixed_source(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return XML
        self.assertEqual(ecb_rates(fetch), DATA)
        self.assertEqual(urls, [SOURCE_URL])

    def test_rejects_bad_xml_dates_missing_duplicate_or_nonfinite_rates(self):
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat().encode()
        for raw in [b'{}', b'<html>unavailable</html>', XML.replace(b'9.75', b'NaN'),
                    XML.replace(b'9.75', b'Infinity'), XML.replace(b'9.75', b'0'),
                    XML.replace(b'9.75', b'-1'), XML.replace(b'currency="CNY"', b'currency="JPY"'),
                    XML.replace(b'currency="GBP"', b'currency="CNY"'),
                    XML.replace(DAY.encode(), b'2025-02-30'), XML.replace(DAY.encode(), tomorrow),
                    XML.replace(DAY.encode(), b'1990-01-01'),
                    b'<!DOCTYPE document [<!ENTITY x "example">]>' + XML,
                    b' ' * (MAX_RESPONSE_BYTES + 1)]:
            with self.subTest(raw=raw[:100]), self.assertRaises((ValueError, ElementTree.ParseError)):
                ecb_rates(lambda _: raw)


class FxCacheTests(unittest.TestCase):
    def setUp(self):
        from apps.api import fx
        self.fx = fx
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'fx.db'
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(self.path), 'ACCUFOLIO_PASSWORD': 'fx-test'})
        self.env.start()
        self.now = time.time()
        self.clock = patch('apps.api.fx.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.provider = patch('apps.api.fx.ecb_rates', return_value=DATA)
        self.fetch = self.provider.start()
        app = FastAPI()
        app.include_router(fx.router)
        self.client = TestClient(app)
        self.auth = ('admin', 'fx-test')

    def tearDown(self):
        self.client.close()
        self.provider.stop()
        self.clock.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_durable_cache_only_fetches_once_and_reports_date(self):
        first = self.fx.get_fx()
        second = self.fx.get_fx()
        self.assertEqual(first['rates'], DATA['rates'])
        self.assertTrue(first['available'])
        self.assertFalse(first['stale'])
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(first['as_of'], DAY)
        self.assertEqual(first['fetched_at'], second['fetched_at'])
        self.fetch.assert_called_once_with()
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(json.loads(con.execute('SELECT payload FROM display_fx_cache').fetchone()[0]), DATA)

    def test_failure_returns_original_cache_with_explicit_stale_and_backoff(self):
        first = self.fx.get_fx()
        self.now += self.fx.TTL_SECONDS + 1
        self.fetch.side_effect = TimeoutError('provider details must not leak')
        failed = self.fx.get_fx()
        self.assertEqual(first['rates'], failed['rates'])
        self.assertEqual(first['fetched_at'], failed['fetched_at'])
        self.assertTrue(failed['stale'])
        self.assertTrue(failed['cached'])
        self.assertIn('TimeoutError', failed['errors'][0])
        self.assertNotIn('details', failed['errors'][0])
        self.fx.get_fx()
        self.assertEqual(self.fetch.call_count, 2)
        self.now += self.fx.RETRY_SECONDS + 1
        self.fetch.side_effect = None
        recovered = self.fx.get_fx()
        self.assertFalse(recovered['stale'])
        self.assertEqual(recovered['errors'], [])
        self.assertEqual(self.fetch.call_count, 3)

    def test_cold_failure_only_usd_available_no_fabricated_conversion(self):
        self.fetch.side_effect = OSError('offline')
        response = self.client.get('/api/fx', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['available'])
        self.assertEqual(body['rates'], {'USD': 1})
        self.assertIsNone(body['as_of'])
        self.assertIsNone(body['fetched_at'])
        self.assertFalse(body['cached'])
        self.fx.get_fx()
        self.assertEqual(self.fetch.call_count, 1)

    def test_auth_and_integrated_route(self):
        self.assertEqual(self.client.get('/api/fx').status_code, 401)
        self.fetch.assert_not_called()
        from apps.api.main import app
        client = TestClient(app, raise_server_exceptions=True)
        try:
            response = client.get('/api/fx', auth=self.auth)
        finally:
            client.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['rates']['CNY'], 7.2)

    def test_corrupt_payload_and_future_timestamp_refetch(self):
        self.fx.get_fx()
        for payload, stamp in [('broken', self.now), (json.dumps(DATA), self.now + 1),
                               (json.dumps({**DATA, 'rates': {**DATA['rates'], 'CNY': False}}), self.now)]:
            with closing(sqlite3.connect(self.path)) as con:
                con.execute('UPDATE display_fx_cache SET payload=?,fetched_at=?', (payload, stamp))
                con.commit()
            self.assertEqual(self.fx.get_fx()['rates'], DATA['rates'])
        self.assertEqual(self.fetch.call_count, 4)

    def test_provider_age_not_cache_age_marks_source_stale(self):
        old_day = (datetime.now(timezone.utc).date() - timedelta(days=8)).isoformat()
        self.fetch.return_value = {**DATA, 'as_of': old_day}
        result = self.fx.get_fx()
        self.assertTrue(result['stale'])
        self.assertFalse(result['cached'])
        self.assertEqual(result['as_of'], old_day)

    def test_network_does_not_lock_database(self):
        def fetch():
            with closing(sqlite3.connect(self.path, timeout=.1)) as con:
                con.execute('BEGIN IMMEDIATE')
                con.execute('CREATE TABLE concurrent_write(value INTEGER)')
                con.execute('INSERT INTO concurrent_write VALUES(1)')
                con.commit()
            return DATA
        self.fetch.side_effect = fetch
        self.assertTrue(self.fx.get_fx()['available'])

    def test_concurrent_callers_share_fetch(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test release')
            return DATA
        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = [pool.submit(self.fx.get_fx) for _ in range(3)]
            self.assertTrue(entered.wait(3))
            release.set()
            for future in results:
                self.assertEqual(future.result(timeout=4)['rates'], DATA['rates'])
        self.fetch.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
