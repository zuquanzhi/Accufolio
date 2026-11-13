"""Observable background maintenance and conservative retention policies."""
from datetime import datetime, timezone
from contextlib import closing
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from packages import notifications

_worker_context = threading.local()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def worker_schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS background_worker(
        id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
        started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
        scan_started_at TEXT, last_success_at TEXT,
        last_error_at TEXT, last_error TEXT, interval_seconds INTEGER NOT NULL)''')


def worker_started(interval):
    from apps.api.main import db
    now = utc_now()
    with db() as con:
        worker_schema(con)
        con.execute('''INSERT INTO background_worker(id,status,started_at,heartbeat_at,interval_seconds)
            VALUES(1,'idle',?,?,?) ON CONFLICT(id) DO UPDATE SET
            status='idle',started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,
            scan_started_at=NULL,interval_seconds=excluded.interval_seconds''', (now, now, interval))


def mark_worker(status, error=None):
    from apps.api.main import db
    now = utc_now()
    with db() as con:
        worker_schema(con)
        con.execute('UPDATE background_worker SET status=?,heartbeat_at=? WHERE id=1', (status, now))
        if status == 'running':
            con.execute('UPDATE background_worker SET scan_started_at=? WHERE id=1', (now,))
        if status == 'idle':
            con.execute('UPDATE background_worker SET last_success_at=? WHERE id=1', (now,))
        if error:
            con.execute('UPDATE background_worker SET last_error_at=?,last_error=? WHERE id=1', (now, error))


def update_scan_progress():
    """Only the scheduler may refresh its heartbeat; a manual scan cannot mask a dead worker."""
    if not getattr(_worker_context, 'active', False):
        return
    from apps.api.main import db
    with db() as con:
        worker_schema(con)
        con.execute('UPDATE background_worker SET heartbeat_at=? WHERE id=1', (utc_now(),))


def worker_status():
    from apps.api.main import db
    with db() as con:
        worker_schema(con)
        row = con.execute('SELECT * FROM background_worker WHERE id=1').fetchone()
    if not row:
        return {'status': 'not_started', 'heartbeat_at': None, 'last_success_at': None, 'last_error': None}
    result = dict(row)
    result.pop('id')
    # Per-symbol progress refreshes this heartbeat, so a large watchlist may run
    # for longer than one interval without falsely appearing to have stopped.
    result['stale_after_seconds'] = max(300, result['interval_seconds'] * 3)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(result['heartbeat_at'])).total_seconds()
    result['heartbeat_age_seconds'] = max(0, round(age))
    if result['status'] != 'stopped' and age > result['stale_after_seconds']:
        result['status'] = 'stale'
    return result


def backup_retention(directory: Path, keep=None):
    """Rotate verified automatic daily backups only; manual/foreign files survive."""
    keep = max(1, int(os.environ.get('ACCUFOLIO_BACKUP_KEEP', '30'))) if keep is None else max(1, keep)
    directory = directory.resolve()
    if not directory.exists():
        return 0
    successful = []
    for path in directory.iterdir():
        if not re.fullmatch(r'accufolio-\d{4}-\d{2}-\d{2}\.db', path.name):
            continue
        if path.is_symlink() or not path.is_file() or path.resolve().parent != directory:
            continue
        try:
            datetime.strptime(path.name[10:-3], '%Y-%m-%d')
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
                if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    continue
            successful.append(path)
        except (ValueError, sqlite3.Error):
            continue
    removed = 0
    for path in sorted(successful, key=lambda item: item.name, reverse=True)[keep:]:
        path.unlink()
        removed += 1
    return removed


def daily_backup(day):
    from apps.api.main import ROOT
    from apps.api.extended import backup
    directory = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT / 'data' / 'accufolio.db'))).parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'accufolio-{day}.db'
    staging = directory / ('.' + destination.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        backup(staging)
        with closing(sqlite3.connect(staging)) as con:
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Daily backup integrity check failed')
        staging.replace(destination)
        backup_retention(directory)
    finally:
        if staging.exists():
            staging.unlink()


def cleanup_alerts(retention_days=None, maximum=None):
    """Bound routed/sent history without discarding queued, active or failed delivery."""
    from apps.api.main import db
    days = max(1, int(os.environ.get('ACCUFOLIO_ALERT_RETENTION_DAYS', '365'))) if retention_days is None else max(1, retention_days)
    cap = max(1, int(os.environ.get('ACCUFOLIO_ALERT_MAX_ROWS', '10000'))) if maximum is None else max(1, maximum)
    with db() as con:
        notifications.ensure_schema(con)
        con.execute('BEGIN IMMEDIATE')
        con.execute('CREATE TEMP TABLE expired_alert_ids(id INTEGER PRIMARY KEY)')
        con.execute('''INSERT INTO expired_alert_ids SELECT a.id FROM alerts a
            WHERE NOT EXISTS (SELECT 1 FROM notification_outbox n WHERE n.alert_id=a.id AND n.status<>'sent')
            AND (a.ts < datetime('now',?) OR a.id NOT IN (SELECT id FROM alerts ORDER BY id DESC LIMIT ?))''',
                    (f'-{days} days', cap))
        con.execute('DELETE FROM notification_outbox WHERE alert_id IN (SELECT id FROM expired_alert_ids)')
        removed = con.execute('DELETE FROM alerts WHERE id IN (SELECT id FROM expired_alert_ids)').rowcount
    return removed


def report_worker_failure():
    """Best effort durable notification; never include exception/credential text."""
    from apps.api.main import db
    from apps.api.extended import record_alert
    day = datetime.now(timezone.utc).date().isoformat()
    with db() as con:
        record_alert(con, 'SYSTEM', '后台维护任务失败，请检查运行状态与服务日志。',
                     f'{day}:system:worker-failure', 'system', as_of=day)
    notifications.deliver_pending(db)


def run_worker(stop, interval):
    from apps.api.extended import scan_all
    _worker_context.active = True
    last_backup = None
    initialized = False
    try:
        while not stop.is_set():
            try:
                if not initialized:
                    worker_started(interval)
                    initialized = True
                mark_worker('running')
                errors = []
                day = datetime.now(timezone.utc).date().isoformat()
                if last_backup != day:
                    try:
                        daily_backup(day)
                        last_backup = day
                    except Exception:
                        logging.exception('Accufolio automatic backup failed')
                        errors.append('自动备份未完成')
                try:
                    update_scan_progress()
                    scan_all()
                except Exception:
                    logging.exception('Accufolio background scan failed')
                    errors.append('自动监测未完成')
                if errors:
                    mark_worker('failed', '；'.join(errors))
                    report_worker_failure()
                else:
                    mark_worker('idle')
            except Exception:
                # If SQLite itself fails, health will expose the stale heartbeat
                # when it recovers; do not manufacture a successful cycle.
                logging.exception('Accufolio background maintenance failed')
            stop.wait(interval)
    finally:
        _worker_context.active = False
        try:
            mark_worker('stopped')
        except Exception:
            logging.exception('Accufolio could not persist worker shutdown')
