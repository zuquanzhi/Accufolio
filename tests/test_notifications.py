import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from apps.api.main import db
from packages import notifications as delivery


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(self.tmp.name) / 'test.db'),
            'ACCUFOLIO_WEBHOOK_URL': 'https://notify.example.test/events?secret=url-secret',
            'ACCUFOLIO_WEBHOOK_TOKEN': 'header-secret'})
        self.env.start()
        self.transport = patch('packages.notifications.send_webhook', return_value=204)
        self.send = self.transport.start()

    def tearDown(self):
        self.transport.stop()
        self.env.stop()
        self.tmp.cleanup()

    def enqueue(self, key='one'):
        with db() as con:
            cursor = con.execute('INSERT INTO alerts(symbol,message,dedupe) VALUES(?,?,?)', ('QQQM', 'L1 alert', key))
            delivery.enqueue(con, cursor.lastrowid, 'QQQM', 'L1 alert', 'move', 1, '2026-11-13')

    def rows(self):
        with db() as con:
            delivery.ensure_schema(con)
            return [dict(row) for row in con.execute('SELECT * FROM notification_outbox')]

    def test_disabled_never_enqueues_or_connects(self):
        with patch.dict(os.environ, {'ACCUFOLIO_WEBHOOK_URL': ''}):
            self.enqueue()
            self.assertEqual(delivery.deliver_pending(db), 0)
            self.assertFalse(delivery.delivery_status(db)['configured'])
            self.assertEqual(self.rows(), [])
        self.send.assert_not_called()

    def test_durable_success_and_safe_status(self):
        self.enqueue()
        first = self.rows()[0]
        self.assertEqual(delivery.deliver_pending(db, now=1000), 1)
        delivery.deliver_pending(db, now=2000)
        self.send.assert_called_once()
        payload = json.loads(self.send.call_args.args[1])
        self.assertEqual(payload['event_id'], first['event_id'])
        self.assertEqual(payload['alert']['level'], 1)
        self.assertEqual(self.rows()[0]['status'], 'sent')
        status = delivery.delivery_status(db)
        self.assertEqual(status['sent'], 1)
        self.assertNotIn('url-secret', json.dumps(status))
        self.assertNotIn('header-secret', json.dumps(status))
        self.assertNotIn('notify.example', json.dumps(self.rows()))

    def test_failure_retries_with_stable_event_and_backoff(self):
        self.enqueue()
        self.send.side_effect = [RuntimeError('https://secret.invalid/header-secret'), 200]
        self.assertEqual(delivery.deliver_pending(db, now=1000), 0)
        first = self.rows()[0]
        self.assertEqual(first['status'], 'pending')
        self.assertEqual(first['next_attempt'], 1060)
        self.assertEqual(first['last_error'], 'Webhook transport failed')
        delivery.deliver_pending(db, now=1059)
        self.assertEqual(self.send.call_count, 1)
        delivery.deliver_pending(db, now=1060)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.send.call_args_list[0].args[2], self.send.call_args_list[1].args[2])
        self.assertEqual(self.rows()[0]['status'], 'sent')
        self.assertIsNone(delivery.delivery_status(db)['last_error'])

    def test_exhaustion_and_manual_retry_preserve_identity(self):
        self.enqueue()
        self.send.return_value = 503
        for attempt in range(delivery.MAX_ATTEMPTS):
            delivery.deliver_pending(db, now=1000 + attempt * 4000)
        row = self.rows()[0]
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['attempts'], 8)
        self.assertEqual(delivery.delivery_status(db)['failed'], 1)
        delivery.deliver_pending(db, now=100000)
        self.assertEqual(self.send.call_count, 8)
        self.assertEqual(delivery.retry_failed(db), 1)
        self.send.return_value = 204
        delivery.deliver_pending(db, now=100001)
        self.assertEqual(self.rows()[0]['event_id'], row['event_id'])
        self.assertEqual(self.rows()[0]['status'], 'sent')

    def test_invalid_config_is_visible_without_disclosing_url(self):
        for url in ['http://notify.example.test/key-secret', 'https://user:pass@example.test',
                    'https://example.test/#secret', 'https://example.test:999999/path', 'not-a-url']:
            with self.subTest(url=url), patch.dict(os.environ, {'ACCUFOLIO_WEBHOOK_URL': url}):
                status = delivery.delivery_status(db)
                self.assertTrue(status['configured'])
                self.assertFalse(status['valid'])
                self.assertNotIn(url, status['last_error'])
                delivery.deliver_pending(db)
        self.send.assert_not_called()

    def test_invalid_token_does_not_connect_or_expose_secret(self):
        with patch.dict(os.environ, {'ACCUFOLIO_WEBHOOK_TOKEN': 'private-secret\ninjected'}):
            self.enqueue()
            delivery.deliver_pending(db)
            status = delivery.delivery_status(db)
        self.send.assert_not_called()
        self.assertFalse(status['valid'])
        self.assertNotIn('private-secret', str(status))

    def test_concurrent_workers_claim_once_and_release_db_before_network(self):
        self.enqueue()
        entered, finish = threading.Event(), threading.Event()
        errors = []
        def transport(*args):
            # This second connection can write while the request is in flight.
            with db() as con:
                con.execute("INSERT INTO audit(actor,action,details) VALUES('test','network','{}')")
            entered.set()
            if not finish.wait(5):
                raise RuntimeError('test timed out')
            return 200
        def worker():
            try:
                delivery.deliver_pending(db, now=1000)
            except Exception as exc:
                errors.append(exc)
        self.send.side_effect = transport
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(delivery.deliver_pending(db, now=1001), 0)
        finally:
            finish.set()
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertFalse(thread.is_alive())
        self.send.assert_called_once()
        self.assertEqual(self.rows()[0]['status'], 'sent')

    def test_expired_claim_recovered(self):
        self.enqueue()
        with db() as con:
            con.execute("UPDATE notification_outbox SET status='sending',claim_token='dead',claimed_until=1000")
        delivery.deliver_pending(db, now=1001)
        self.assertEqual(self.rows()[0]['status'], 'sent')

    def test_queue_rolls_back_with_alert_and_duplicate_enqueue_is_ignored(self):
        with self.assertRaises(RuntimeError):
            with db() as con:
                delivery.ensure_schema(con)
                con.execute('BEGIN IMMEDIATE')
                con.execute("INSERT INTO alerts(id,symbol,message,dedupe) VALUES(1,'QQQM','test','rollback')")
                delivery.enqueue(con, 1, 'QQQM', 'test', 'move')
                raise RuntimeError('rollback')
        self.assertEqual(self.rows(), [])
        self.enqueue()
        with db() as con:
            delivery.enqueue(con, 1, 'QQQM', 'duplicate', 'move')
        self.assertEqual(len(self.rows()), 1)


class TransportTests(unittest.TestCase):
    def test_transport_headers_timeout_and_no_redirect(self):
        response = Mock()
        response.status = 204
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = manager
        with patch('packages.notifications.build_opener', return_value=opener) as build:
            config = delivery.WebhookConfig('https://example.test/path', 'secret')
            self.assertEqual(delivery.send_webhook(config, '{"event_id":"one"}', 'one'), 204)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        self.assertEqual(request.get_header('Idempotency-key'), 'one')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 10)
        handler = build.call_args.args[0]
        self.assertIsNone(handler.redirect_request(request, None, 302, '', {}, 'https://other.test'))
