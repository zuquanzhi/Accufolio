import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from apps.api.main import db
from apps.api.extended import scan_all, signals
from apps.api import signals as signal_module
from packages.core import rules
from packages.providers import Quote, QuoteResult


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(self.tmp.name) / 'test.db'),
            'ACCUFOLIO_WEBHOOK_URL': 'https://notify.example.test/events', 'ACCUFOLIO_WEBHOOK_TOKEN': ''})
        self.env.start()
        self.transport = patch('packages.notifications.send_webhook', return_value=204)
        self.send = self.transport.start()
        with db() as con:
            con.execute("INSERT INTO watchlist VALUES('QQQM',2)")

    def tearDown(self):
        self.transport.stop()
        self.env.stop()
        self.tmp.cleanup()

    def scan(self, change, calm=None):
        with patch('apps.api.extended.get_quote', return_value=QuoteResult(Quote('QQQM', 90, change, 'test', 0), False, ())), \
             patch('apps.api.extended.evaluate_calm', return_value=calm or {'status': 'clear', 'as_of': '2026-11-13'}):
            return scan_all()

    def test_actual_movement_rule_escalates_without_lower_level_repeats(self):
        with patch('packages.core.rules.movement_level', wraps=rules.movement_level) as rule:
            for change in [2, 2.5, 6, 4, 1, 6, -2, -6]:
                self.scan(change)
        self.assertEqual(rule.call_count, 8)
        with db() as con:
            messages = [row['message'] for row in con.execute('SELECT * FROM alerts ORDER BY id')]
            self.assertEqual(con.execute('SELECT count(*) FROM notification_outbox').fetchone()[0], 4)
        self.assertEqual([message[:2] for message in messages], ['L1', 'L3', 'L1', 'L3'])
        self.assertEqual(self.send.call_count, 4)
        self.assertEqual(signals(actor='test')[0]['movement_level'], 3)

    def test_calm_dedupes_same_common_close_and_exposes_skipped_reason(self):
        calm = {'status': 'triggered', 'as_of': '2026-11-13'}
        self.scan(0, calm)
        self.scan(0, calm)
        self.scan(0, {'status': 'skipped', 'reason': 'VIX 日线暂不可用'})
        self.assertEqual(self.send.call_count, 1)
        state = signals(actor='test')[0]['calm']
        self.assertEqual(state['status'], 'skipped')
        self.assertIn('VIX', state['reason'])

    def test_source_failures_still_alert_and_dedupe(self):
        with patch('apps.api.extended.get_quote', return_value=QuoteResult(None, True, ('offline',))), \
             patch('apps.api.extended.evaluate_calm', return_value={'status': 'skipped', 'reason': 'offline'}):
            for _ in range(2):
                scan_all()
        self.send.assert_called_once()

    def test_concurrent_scans_insert_and_deliver_only_one_alert(self):
        with patch('apps.api.extended.get_quote', return_value=QuoteResult(Quote('QQQM', 90, 6, 'test', 0), False, ())), \
             patch('apps.api.extended.evaluate_calm', return_value={'status': 'clear', 'as_of': '2026-11-13'}):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: scan_all(), range(2)))
        self.assertEqual([row[0]['movement_level'] for row in results], [3, 3])
        with db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM alerts').fetchone()[0], 1)
        self.send.assert_called_once()


class CalmTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 13)
        days = [(self.today - timedelta(days=i)).isoformat() for i in range(19, -1, -1)]
        self.stock = {'bars': [{'day': day, 'close': 100 if i < 19 else 90} for i, day in enumerate(days)], 'stale': False}
        self.vix = {'bars': [{'day': day, 'close': 30 if i < 19 else 20} for i, day in enumerate(days)], 'stale': False}

    def evaluate(self):
        with patch('apps.api.history.get_history', return_value=self.stock), \
             patch('apps.api.signals.get_vix_history', return_value=self.vix):
            return signal_module.evaluate_calm('QQQM', today=self.today)

    def test_actual_calm_rule_uses_twenty_aligned_closes(self):
        with patch('packages.core.rules.calm_signal', wraps=rules.calm_signal) as rule:
            result = self.evaluate()
        self.assertEqual(result['status'], 'triggered')
        self.assertEqual(result['as_of'], '2026-11-13')
        self.assertEqual(result['window'], 20)
        rule.assert_called_once_with(90, [100] * 19 + [90], 20, 30)

    def test_insufficient_or_stale_data_never_calls_rule(self):
        for source in (self.stock, self.vix):
            source['stale'] = True
            with patch('packages.core.rules.calm_signal') as rule:
                self.assertEqual(self.evaluate()['status'], 'skipped')
                rule.assert_not_called()
            source['stale'] = False
        self.vix['bars'].pop()
        with patch('packages.core.rules.calm_signal') as rule:
            self.assertIn('20', self.evaluate()['reason'])
            rule.assert_not_called()

    def test_old_aligned_data_or_invalid_prices_skip(self):
        self.today += timedelta(days=8)
        self.assertIn('7', self.evaluate()['reason'])
        self.today -= timedelta(days=8)
        self.stock['bars'][-1]['close'] = float('nan')
        self.assertEqual(self.evaluate()['status'], 'skipped')

    def test_unavailable_history_skips_without_vix_network(self):
        with patch('apps.api.history.get_history', side_effect=RuntimeError('offline')), \
             patch('apps.api.signals.get_vix_history') as fetch:
            result = signal_module.evaluate_calm('QQQM', today=self.today)
        self.assertEqual(result['status'], 'skipped')
        fetch.assert_not_called()

    def test_latest_prices_must_share_the_same_date(self):
        # Tomorrow's VIX observation must not replace today's aligned close.
        self.vix['bars'].append({'day': '2026-11-14', 'close': 99})
        result = self.evaluate()
        self.assertEqual(result['vix'], 20)
        self.assertEqual(result['as_of'], '2026-11-13')

    def test_suspected_split_is_skipped(self):
        self.stock['bars'][-1]['close'] = 20
        with patch('packages.core.rules.calm_signal') as rule:
            self.assertIn('拆股', self.evaluate()['reason'])
            rule.assert_not_called()


class VixTests(unittest.TestCase):
    def test_csv_parsing_validates_values_and_dates(self):
        bars = signal_module.parse_vix(b'DATE,OPEN,HIGH,LOW,CLOSE\n11/13/2026,1,1,1,20\n11/12/2026,1,1,1,30\n')
        self.assertEqual(bars, [{'day': '2026-11-12', 'close': 30}, {'day': '2026-11-13', 'close': 20}])
        for raw in [b'<html>unavailable</html>', b'DATE,CLOSE\n11/13/2026,nan',
                    b'DATE,CLOSE\n11/13/2026,0', b'DATE,CLOSE\n13/13/2026,20',
                    b'DATE,CLOSE\n11/13/2026,20\n11/13/2026,21']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                signal_module.parse_vix(raw)

    def test_cache_success_stale_failure_and_cooldown(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(folder) / 'vix.db')}):
            bars = [{'day': '2026-11-13', 'close': 20}]
            with patch('apps.api.signals.time.time', return_value=1000), patch('apps.api.signals.fetch_vix', return_value=bars) as fetch:
                self.assertFalse(signal_module.get_vix_history()['cached'])
                self.assertTrue(signal_module.get_vix_history()['cached'])
                fetch.assert_called_once()
            with patch('apps.api.signals.time.time', return_value=1000 + signal_module.TTL_SECONDS + 1), \
                 patch('apps.api.signals.fetch_vix', side_effect=RuntimeError('offline')) as fetch:
                result = signal_module.get_vix_history()
                self.assertTrue(result['stale'])
                self.assertEqual(result['bars'], bars)
                self.assertTrue(signal_module.get_vix_history()['stale'])
                fetch.assert_called_once()

    def test_invalid_cache_timestamp_or_empty_payload_is_refetched(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(folder) / 'vix.db')}):
            bars = [{'day': '2026-11-13', 'close': 20}]
            with patch('apps.api.signals.time.time', return_value=1000), patch('apps.api.signals.fetch_vix', return_value=bars) as fetch:
                signal_module.get_vix_history()
                for timestamp in [None, float('nan'), 1001]:
                    with self.subTest(timestamp=timestamp):
                        with db() as con:
                            con.execute('UPDATE volatility_cache SET fetched_at=?', (timestamp,))
                        signal_module.get_vix_history()
                with db() as con:
                    con.execute("UPDATE volatility_cache SET payload='[]'")
                signal_module.get_vix_history()
                self.assertEqual(fetch.call_count, 5)
