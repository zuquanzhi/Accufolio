"""Durable, at-least-once Webhook delivery; receivers deduplicate event_id.

Configuration is read at delivery time and never persisted in the outbox.
Network requests run outside database transactions; a leased claim serializes
concurrent workers. An expired claim may be retried after an interrupted send.
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_ATTEMPTS = 8
TIMEOUT_SECONDS = 10
CLAIM_SECONDS = 120


@dataclass(frozen=True)
class WebhookConfig:
    url: str = field(repr=False)
    token: str = field(repr=False)
    error: str | None = None

    @property
    def enabled(self):
        return bool(self.url)


def configuration():
    url = os.environ.get('ACCUFOLIO_WEBHOOK_URL', '').strip()
    token = os.environ.get('ACCUFOLIO_WEBHOOK_TOKEN', '').strip()
    error = None
    if url:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.fragment or parsed.port == 0
                    or any(ord(c) <= 32 for c in url)):
                raise ValueError()
        except ValueError:
            error = 'Webhook URL must be an HTTPS URL without user information or a fragment'
    if any(ord(c) < 32 or ord(c) > 126 for c in token):
        error = 'Webhook token must contain printable ASCII characters only'
    return WebhookConfig(url, token, error)


def ensure_schema(con):
    # execute rather than executescript: callers may already be in the alert transaction.
    con.execute('''CREATE TABLE IF NOT EXISTS notification_outbox(
        id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL UNIQUE,
        event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0, claim_token TEXT, claimed_until REAL,
        last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        sent_at TEXT)''')
    con.execute('CREATE INDEX IF NOT EXISTS outbox_due ON notification_outbox(status,next_attempt)')


def enqueue(con, alert_id, symbol, message, kind, level=None, as_of=None):
    """Called in the same transaction as a newly inserted alert."""
    if not configuration().enabled:
        return
    ensure_schema(con)
    event_id = str(uuid.uuid4())
    payload = {'schema': 'accufolio.alert.v1', 'event_id': event_id,
               'created_at': datetime.now(timezone.utc).isoformat(),
               'alert': {'id': alert_id, 'symbol': symbol, 'kind': kind,
                         'level': level, 'message': message, 'as_of': as_of}}
    con.execute('INSERT OR IGNORE INTO notification_outbox(alert_id,event_id,payload) VALUES(?,?,?)',
                (alert_id, event_id, json.dumps(payload, ensure_ascii=False)))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_webhook(config, payload, event_id):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Accufolio-Webhook/1',
               'Idempotency-Key': event_id}
    if config.token:
        headers['Authorization'] = 'Bearer ' + config.token
    request = Request(config.url, data=payload.encode('utf-8'), headers=headers, method='POST')
    with build_opener(NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def deliver_pending(db, limit=8, now=None):
    """Claim due rows, deliver, then acknowledge. Never return transport exceptions."""
    config = configuration()
    if not config.enabled or config.error:
        return 0
    delivered = 0
    for _ in range(limit):
        current = time.time() if now is None else now
        claim = str(uuid.uuid4())
        with db() as con:
            ensure_schema(con)
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('''SELECT * FROM notification_outbox WHERE
                (status='pending' AND next_attempt<=?) OR
                (status='sending' AND claimed_until<=?) ORDER BY id LIMIT 1''',
                (current, current)).fetchone()
            if row is None:
                break
            con.execute("UPDATE notification_outbox SET status='sending',claim_token=?,claimed_until=? WHERE id=?",
                        (claim, current + CLAIM_SECONDS, row['id']))
        error = None
        try:
            status = send_webhook(config, row['payload'], row['event_id'])
            if not 200 <= status < 300:
                error = f'HTTP {status}'
        except HTTPError as exc:
            error = f'HTTP {exc.code}'
            exc.close()
        except Exception:
            # Exceptions may contain tokens in URLs, headers or response bodies.
            error = 'Webhook transport failed'
        attempts = row['attempts'] + 1
        finished = time.time() if now is None else now
        state = 'sent' if error is None else ('failed' if attempts >= MAX_ATTEMPTS else 'pending')
        delay = min(3600, 60 * 2 ** min(attempts - 1, 6))
        with db() as con:
            con.execute('''UPDATE notification_outbox SET status=?,attempts=?,next_attempt=?,
                last_error=?,sent_at=?,claim_token=NULL,claimed_until=NULL WHERE id=? AND claim_token=?''',
                (state, attempts, finished + delay, error,
                 datetime.now(timezone.utc).isoformat() if error is None else None, row['id'], claim))
        if error is None:
            delivered += 1
    return delivered


def delivery_status(db):
    config = configuration()
    with db() as con:
        ensure_schema(con)
        counts = {r['status']: r['total'] for r in con.execute(
            'SELECT status,count(*) AS total FROM notification_outbox GROUP BY status')}
        last = con.execute('''SELECT last_error FROM notification_outbox
            WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT 1''').fetchone()
    return {'configured': config.enabled, 'valid': not bool(config.error),
            **{state: counts.get(state, 0) for state in ('pending', 'sending', 'sent', 'failed')},
            'last_error': config.error or (last['last_error'] if last else None)}


def retry_failed(db):
    with db() as con:
        ensure_schema(con)
        count = con.execute("UPDATE notification_outbox SET status='pending',attempts=0,next_attempt=0 WHERE status='failed'").rowcount
    return count
