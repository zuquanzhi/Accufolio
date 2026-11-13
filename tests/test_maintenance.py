"""Background observability and retention without losing pending notifications."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from apps.api.main import db
from apps.api import maintenance
from apps.api.extended import health, scan_all
from packages import notifications
from packages.providers import Quote, QuoteResult


class CycleStop:
    def __init__(self, count=1):
        self.remaining = count

    def is_set(self):
        return self.remaining <= 0

    def wait(self, interval):
        self.remaining -= 1


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(self.base / 'test.db'),
                              'ACCUFOLIO_WEBHOOK_URL': '', 'ACCUFOLIO_PASSWORD': 'test'})
        self.env.start()

    def tearDown(self):
        maintenance._worker_context.active = False
        self.env.stop()
        self.temp.cleanup()

    def test_health_reports_not_started_running_stale_failure_and_recovery(self):
        self.assertEqual(health(actor='test')['worker']['status'], 'not_started')
        maintenance.worker_started(300)
        maintenance.mark_worker('running')
        state = health(actor='test')['worker']
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['stale_after_seconds'], 900)
        self.assertIsNotNone(state['scan_started_at'])
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db() as con:
            con.execute('UPDATE background_worker SET heartbeat_at=?', (old,))
        self.assertEqual(maintenance.worker_status()['status'], 'stale')
        maintenance.mark_worker('failed', '自动监测未完成')
        self.assertEqual(maintenance.worker_status()['status'], 'failed')
        maintenance.mark_worker('idle')
        self.assertIsNotNone(maintenance.worker_status()['last_success_at'])

    def test_manual_scan_cannot_hide_stalled_worker(self):
        maintenance.worker_started(300)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db() as con:
            con.execute('UPDATE background_worker SET heartbeat_at=?', (old,))
        maintenance.update_scan_progress()
        self.assertEqual(maintenance.worker_status()['status'], 'stale')
        maintenance._worker_context.active = True
        maintenance.update_scan_progress()
        self.assertNotEqual(maintenance.worker_status()['status'], 'stale')

    def test_daily_backup_rotates_only_verified_automatic_files(self):
        directory = self.base / 'backups'
        directory.mkdir()
        for index in range(35):
            day = date(2025, 1, 1) + timedelta(days=index)
            with closing(sqlite3.connect(directory / f'accufolio-{day.isoformat()}.db')) as con:
                con.execute('CREATE TABLE valid(id INTEGER)')
        manual = directory / 'accufolio-20240101-000000.db'
        invalid = directory / 'accufolio-2020-01-01.db'
        unrelated = directory / 'other.db'
        for path in [manual, invalid, unrelated]:
            path.write_text('preserve', encoding='utf-8')
        self.assertEqual(maintenance.backup_retention(directory, keep=30), 5)
        for path in [manual, invalid, unrelated]:
            self.assertTrue(path.exists())
        self.assertFalse((directory / 'accufolio-2025-01-01.db').exists())
        self.assertTrue((directory / 'accufolio-2025-02-04.db').exists())

    def test_backup_publish_is_atomic_and_bad_attempt_does_not_replace_good_copy(self):
        maintenance.daily_backup('2026-01-01')
        path = self.base / 'backups/accufolio-2026-01-01.db'
        before = path.read_bytes()
        with patch('apps.api.extended.backup', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                maintenance.daily_backup('2026-01-01')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_alert_age_and_count_keep_every_unsent_outbox_entry(self):
        with db() as con:
            notifications.ensure_schema(con)
            for index in range(1, 13):
                old = index <= 6
                con.execute('INSERT INTO alerts(id,symbol,message,dedupe,ts) VALUES(?,?,?,?,?)',
                            (index, 'TEST', 'test', str(index), '2020-01-01 00:00:00' if old else '2099-01-01 00:00:00'))
            for index, status in [(1, 'pending'), (2, 'sending'), (3, 'failed'), (4, 'sent'), (12, 'sent')]:
                con.execute('INSERT INTO notification_outbox(alert_id,event_id,payload,status) VALUES(?,?,?,?)',
                            (index, str(index), '{}', status))
        removed = maintenance.cleanup_alerts(retention_days=365, maximum=4)
        self.assertEqual(removed, 5)
        with db() as con:
            self.assertEqual([r[0] for r in con.execute('SELECT id FROM alerts ORDER BY id')], [1, 2, 3, 9, 10, 11, 12])
            self.assertEqual([r[0] for r in con.execute('SELECT alert_id FROM notification_outbox ORDER BY alert_id')], [1, 2, 3, 12])

    def test_backup_failure_does_not_block_scan_and_failure_is_durable(self):
        with patch('apps.api.maintenance.daily_backup', side_effect=RuntimeError('secret value')), \
             patch('apps.api.extended.scan_all', return_value=[]) as scan, \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(), 300)
        scan.assert_called_once()
        state = maintenance.worker_status()
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['last_error'], '自动备份未完成')
        self.assertNotIn('secret', str(state))
        with db() as con:
            alerts = list(con.execute('SELECT * FROM alerts'))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['symbol'], 'SYSTEM')

    def test_scan_failure_notifies_once_per_day_and_next_cycle_recovers(self):
        with patch.dict(os.environ, {'ACCUFOLIO_WEBHOOK_URL': 'https://notify.example.test'}), \
             patch('apps.api.maintenance.daily_backup') as backup, \
             patch('apps.api.extended.scan_all', side_effect=[RuntimeError('private'), RuntimeError('private'), []]), \
             patch('packages.notifications.send_webhook', return_value=204) as send, \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(3), 300)
        backup.assert_called_once()
        send.assert_called_once()
        self.assertIsNotNone(maintenance.worker_status()['last_success_at'])

    def test_temporary_initial_database_failure_does_not_kill_worker(self):
        original = maintenance.worker_started
        with patch('apps.api.maintenance.worker_started', side_effect=[RuntimeError('busy'), None]) as start, \
             patch('apps.api.maintenance.daily_backup'), \
             patch('apps.api.extended.scan_all', return_value=[]) as scan, \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(2), 300)
        self.assertEqual(start.call_count, 2)
        scan.assert_called_once()

    def test_valid_fallback_quote_still_triggers_movement_rule(self):
        with db() as con:
            con.execute("INSERT INTO watchlist VALUES('QQQM',2)")
        with patch('apps.api.extended.get_quote', return_value=QuoteResult(Quote('QQQM', 100, 4.2, 'Tencent', 0), True, ('primary source failed',))), \
             patch('apps.api.extended.evaluate_calm', return_value={'status': 'skipped', 'reason': 'fixture'}), \
             patch('packages.core.market_time.et_today', return_value=date(2026, 1, 2)):
            result = scan_all()
        self.assertEqual(result[0]['movement_level'], 2)
        with db() as con:
            rows = list(con.execute('SELECT dedupe FROM alerts'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[0].startswith('2026-01-02:') for row in rows))


if __name__ == '__main__':
    unittest.main()
