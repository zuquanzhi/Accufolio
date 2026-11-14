"""Backup integrity, retries and heartbeat after retiring price monitoring."""
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
from apps.api.extended import health


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
                                          'ACCUFOLIO_PASSWORD': 'test'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_health_reports_not_started_running_stale_failure_and_recovery(self):
        self.assertEqual(health(actor='test')['worker']['status'], 'not_started')
        maintenance.worker_started(300)
        maintenance.mark_worker('running')
        state = health(actor='test')['worker']
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['stale_after_seconds'], 900)
        self.assertIsNotNone(state['cycle_started_at'])
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db() as con:
            con.execute('UPDATE backup_worker SET heartbeat_at=?', (old,))
        self.assertEqual(maintenance.worker_status()['status'], 'stale')
        maintenance.mark_worker('failed', '自动备份未完成')
        self.assertEqual(maintenance.worker_status()['status'], 'failed')
        maintenance.mark_worker('idle')
        self.assertIsNotNone(maintenance.worker_status()['last_success_at'])

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

    def test_backup_failure_is_visible_and_contains_no_exception_secrets(self):
        with patch('apps.api.maintenance.daily_backup', side_effect=RuntimeError('secret value')), \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(), 300)
        state = health(actor='test')
        self.assertEqual(state['worker']['status'], 'stopped')
        self.assertEqual(state['worker']['last_error'], '自动备份未完成')
        self.assertEqual(state['backup']['status'], 'failed')
        self.assertIsNone(state['backup']['last_success_at'])
        self.assertNotIn('secret', str(state))

    def test_failed_backup_retries_then_only_one_successful_copy_per_day(self):
        states = []
        real_backup = maintenance.daily_backup
        def attempt(day):
            states.append(maintenance.backup_status())
            if len(states) < 3:
                raise RuntimeError('private')
            return real_backup(day)
        with patch('apps.api.maintenance.daily_backup', side_effect=attempt) as backup, \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(5), 300)
        self.assertEqual(backup.call_count, 3)
        self.assertEqual([state['status'] for state in states], ['not_created','failed','failed'])
        state = maintenance.backup_status()
        self.assertEqual(state['status'], 'ok')
        self.assertIsNone(state['last_error'])
        self.assertEqual(state['last_backup_date'],datetime.now(timezone.utc).date().isoformat())
        with closing(sqlite3.connect(self.base / 'backups' / state['filename'])) as con:
            self.assertEqual(con.execute('PRAGMA quick_check').fetchone()[0],'ok')
        self.assertIsNotNone(maintenance.worker_status()['last_success_at'])

    def test_failed_next_backup_preserves_last_success(self):
        maintenance.record_backup('2026-01-01')
        successful = maintenance.backup_status()
        with patch('apps.api.maintenance.daily_backup', side_effect=RuntimeError('private')), \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(),300)
        state = maintenance.backup_status()
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['last_backup_date'],'2026-01-01')
        self.assertEqual(state['last_success_at'],successful['last_success_at'])

    def test_temporary_initial_database_failure_does_not_kill_worker(self):
        original = maintenance.worker_started
        calls = []
        def start(interval):
            calls.append(interval)
            if len(calls) == 1:
                raise RuntimeError('busy')
            return original(interval)
        with patch('apps.api.maintenance.worker_started', side_effect=start), \
             patch('apps.api.maintenance.daily_backup') as backup, \
             patch('logging.exception'):
            maintenance.run_worker(CycleStop(2), 300)
        self.assertEqual(calls,[300,300])
        backup.assert_called_once()
        self.assertEqual(maintenance.backup_status()['status'],'ok')

    def test_worker_never_fetches_market_data_or_delivers_old_webhook_queue(self):
        # A 2.1 database may retain old tables and pending events. Starting the
        # dashboard must neither delete those records nor try delivering them.
        with db() as con:
            con.execute('CREATE TABLE watchlist(symbol TEXT PRIMARY KEY,threshold REAL)')
            con.execute("INSERT INTO watchlist VALUES('QQQM',2)")
            con.execute('CREATE TABLE notification_outbox(id INTEGER PRIMARY KEY,status TEXT)')
            con.execute("INSERT INTO notification_outbox VALUES(1,'pending')")
            con.execute('CREATE TABLE alerts(id INTEGER PRIMARY KEY,message TEXT)')
            con.execute("INSERT INTO alerts VALUES(1,'preserve')")
            con.execute('CREATE TABLE scans(id INTEGER PRIMARY KEY)')
        with patch.dict(os.environ,{'ACCUFOLIO_WEBHOOK_URL':'https://notify.example.test'}), \
             patch('packages.providers.get_quote') as quote, \
             patch('packages.providers.history.sina_daily') as history, \
             patch('socket.create_connection',side_effect=AssertionError('No background network')) as network:
            maintenance.run_worker(CycleStop(2),300)
            result=health(actor='test')
        quote.assert_not_called();history.assert_not_called();network.assert_not_called()
        self.assertEqual(result['backup']['status'],'ok')
        with db() as con:
            self.assertEqual(con.execute('SELECT status FROM notification_outbox').fetchone()[0],'pending')
            self.assertEqual(con.execute('SELECT message FROM alerts').fetchone()[0],'preserve')
            self.assertEqual(con.execute('SELECT count(*) FROM accounts').fetchone()[0],1)

    def test_new_database_has_no_retired_monitoring_tables(self):
        with db() as con:
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'watchlist','scans','alerts','notification_outbox','monitoring_state'}.isdisjoint(tables))


if __name__ == '__main__':
    unittest.main()
