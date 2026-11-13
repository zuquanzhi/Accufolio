import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.history import get_history, HistoryUnavailable, router, TTL_SECONDS, RETRY_SECONDS
from packages.providers.history import sina_daily, MAX_RESPONSE_BYTES


BARS = [
    {'day': '2025-01-02', 'close': 100.0},
    {'day': '2025-01-03', 'close': 101.5},
    {'day': '2025-01-06', 'close': 102.0},
]


class DailyProviderTests(unittest.TestCase):
    def test_real_sina_shape_and_normalized_symbol(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return b'[{"d":"2025-01-02","o":"99.00","c":"100.00","v":"123"},{"d":"2025-01-03","c":"101.50"}]'
        self.assertEqual(sina_daily(' qqqm ', fetch), BARS[:2])
        self.assertEqual(urls, ['https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK?symbol=qqqm'])

    def test_rejects_bad_symbols_before_fetch(self):
        for symbol in ['../QQQM', 'QQQM&x=1', 'a/b', '', 'A' * 16, 'a b']:
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                sina_daily(symbol, lambda _: self.fail('Network must not run for invalid symbols'))

    def test_rejects_invalid_provider_data(self):
        for raw in [b'null', b'{}', b'[]', b'<html>busy</html>', b'{"error":"bad symbol"}',
                    b'[{"d":"2025-01-02","c":"NaN"}]', b'[{"d":"2025-01-02","c":"Infinity"}]',
                    b'[{"d":"2025-01-02","c":-1}]', b'[{"d":"2025-01-02","c":true}]',
                    b'[{"d":"2025-02-30","c":100}]', b'[{"d":"2025-01-02","c":100},{"d":"2025-01-02","c":101}]',
                    b'[{"d":"2025-01-03","c":100},{"d":"2025-01-02","c":101}]']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sina_daily('QQQM', lambda _: raw)
        with self.assertRaises(ValueError):
            sina_daily('QQQM', lambda _: b' ' * (MAX_RESPONSE_BYTES + 1))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.env = patch.dict(os.environ, {'ACCUFOLIO_PASSWORD': 'history-test', 'ACCUFOLIO_DB': str(self.path)})
        self.env.start()
        self.now = 1794513600.0
        self.clock = patch('apps.api.history.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.provider = patch('apps.api.history.sina_daily', return_value=BARS)
        self.fetch = self.provider.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.auth = ('admin', 'history-test')

    def tearDown(self):
        self.client.close()
        self.provider.stop()
        self.clock.stop()
        self.env.stop()
        self.tmp.cleanup()

    def load(self, **kwargs):
        return get_history('QQQM', kwargs.pop('start', '2025-01-01'), kwargs.pop('end', '2025-01-06'), **kwargs)

    def test_cache_reused_across_requested_ranges(self):
        initial = self.load()
        cached = self.load(start='2025-01-03', end='2025-01-03')
        self.assertEqual(initial['bars'], BARS)
        self.assertFalse(initial['cached'])
        self.assertFalse(initial['stale'])
        self.assertEqual(cached['bars'], BARS[1:2])
        self.assertTrue(cached['cached'])
        self.fetch.assert_called_once_with('QQQM')
        self.assertEqual((cached['available_start'], cached['available_end']), ('2025-01-02', '2025-01-06'))
        self.assertEqual(cached['adjustment'], 'unadjusted')
        self.assertIn('拆股', cached['adjustment_note'])

    def test_expired_cache_failure_preserves_bars_and_retries_after_cooldown(self):
        initial = self.load()
        self.now += TTL_SECONDS + 1
        self.fetch.side_effect = TimeoutError('private provider diagnostics')
        failed = self.load()
        self.assertEqual(failed['bars'], initial['bars'])
        self.assertEqual(failed['fetched_at'], initial['fetched_at'])
        self.assertTrue(failed['stale'])
        self.assertTrue(failed['cached'])
        self.assertIn('TimeoutError', failed['errors'][0])
        self.assertNotIn('private provider diagnostics', failed['errors'][0])
        self.load()
        self.assertEqual(self.fetch.call_count, 2)
        self.now += RETRY_SECONDS + 1
        self.fetch.side_effect = None
        self.fetch.return_value = BARS + [{'day': '2025-01-07', 'close': 103.0}]
        recovered = self.load(end='2025-01-07')
        self.assertFalse(recovered['stale'])
        self.assertFalse(recovered['cached'])
        self.assertEqual(recovered['errors'], [])
        self.assertEqual(len(recovered['bars']), 4)

    def test_cold_failure_is_503_and_uses_negative_cache(self):
        self.fetch.side_effect = OSError('network down')
        response = self.client.get('/api/history', auth=self.auth)
        self.assertEqual(response.status_code, 503)
        self.assertIsInstance(response.json()['detail'], str)
        with self.assertRaises(HistoryUnavailable):
            self.load()
        self.assertEqual(self.fetch.call_count, 1)

    def test_integrated_route_auth_and_expired_cache_response(self):
        from apps.api.main import app
        client = TestClient(app)
        try:
            url = '/api/history?symbol=QQQM&start=2025-01-01&end=2025-01-06'
            self.assertEqual(client.get(url).status_code, 401)
            initial = client.get(url, auth=self.auth)
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.headers['cache-control'], 'no-store')
            self.now += TTL_SECONDS + 1
            self.fetch.side_effect = TimeoutError('offline')
            response = client.get(url, auth=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['bars'], BARS)
            self.assertTrue(response.json()['cached'])
            self.assertTrue(response.json()['stale'])
            self.assertIn('TimeoutError', response.json()['errors'][0])
        finally:
            client.close()

    def test_http_auth_validation_and_empty_range(self):
        self.assertEqual(self.client.get('/api/history').status_code, 401)
        for query in ['symbol=QQQM%26other=x', 'start=garbage', 'start=2025-02-01&end=2025-01-01',
                      'start=1900-01-01&end=2025-01-01', 'end=2099-01-01']:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/history?' + query, auth=self.auth).status_code, 422)
        self.fetch.assert_not_called()
        response = self.client.get('/api/history?symbol=qqqm&start=2024-01-01&end=2024-01-31', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['bars'], [])
        self.assertIn('没有日线', response.json()['errors'][0])

    def test_corrupt_cache_is_refetched(self):
        self.load()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE daily_history_cache SET payload='broken', fetched_at=NULL")
            connection.commit()
        self.assertEqual(self.load()['bars'], BARS)
        self.assertEqual(self.fetch.call_count, 2)

    def test_preclose_cache_never_becomes_a_completed_candle_after_close(self):
        self.now = datetime.fromisoformat('2026-11-13T12:00:00-05:00').timestamp()
        self.fetch.return_value = [{'day':'2026-11-12','close':283.39},
                                   {'day':'2026-11-13','close':278.79}]
        first = self.load(start='2026-11-12',end='2026-11-13')
        self.assertEqual(first['available_end'],'2026-11-12')
        self.assertEqual(len(first['bars']),1)
        self.now = datetime.fromisoformat('2026-11-13T16:01:00-05:00').timestamp()
        self.fetch.side_effect = TimeoutError('not yet published')
        fallback = self.load(start='2026-11-12',end='2026-11-13')
        self.assertTrue(fallback['stale'])
        self.assertEqual(fallback['available_end'],'2026-11-12')
        self.assertEqual(len(fallback['bars']),1)
        self.now += RETRY_SECONDS + 1
        self.fetch.side_effect = None
        self.fetch.return_value = [{'day':'2026-11-12','close':283.39},
                                   {'day':'2026-11-13','close':280.5}]
        refreshed = self.load(start='2026-11-12',end='2026-11-13')
        self.assertEqual(refreshed['bars'][-1]['close'],280.5)
        self.assertEqual(refreshed['available_end'],'2026-11-13')
        self.assertFalse(refreshed['cached'])

    def test_stalled_source_and_large_jump_are_visible(self):
        self.fetch.return_value = BARS + [{'day': '2025-01-07', 'close': 30.0}]
        result = self.load(end='2025-01-20')
        self.assertTrue(result['stale'])
        self.assertTrue(any('停止更新' in warning for warning in result['warnings']))
        self.assertTrue(any('跳变' in warning for warning in result['warnings']))

    def test_provider_call_does_not_hold_sqlite_transaction(self):
        def fetch(_):
            with closing(sqlite3.connect(self.path, timeout=.1)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('CREATE TABLE other_writer(value INTEGER)')
                connection.execute('INSERT INTO other_writer VALUES(1)')
                connection.commit()
            return BARS
        self.fetch.side_effect = fetch
        self.assertEqual(self.load()['bars'], BARS)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT value FROM other_writer').fetchone()[0], 1)

    def test_concurrent_requests_share_one_fetch(self):
        entered = threading.Event()
        release = threading.Event()
        def fetch(_):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return BARS
        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.load)
            self.assertTrue(entered.wait(timeout=5))
            second = pool.submit(self.load)
            release.set()
            self.assertEqual(first.result(timeout=5)['bars'], BARS)
            self.assertEqual(second.result(timeout=5)['bars'], BARS)
        self.fetch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
