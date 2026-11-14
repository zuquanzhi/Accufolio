"""Automatic SQLite backups with visible success, failure and worker heartbeat."""
from datetime import datetime, timezone
from contextlib import closing
import logging
import os
from pathlib import Path
import re
import sqlite3
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def worker_schema(con):
    # Separate from retired monitoring state. Existing databases keep their old
    # tables untouched; this service neither reads nor drains a former outbox.
    con.execute('''CREATE TABLE IF NOT EXISTS backup_worker(
        id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
        started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
        cycle_started_at TEXT, last_success_at TEXT,
        last_error_at TEXT, last_error TEXT, interval_seconds INTEGER NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS backup_state(
        id INTEGER PRIMARY KEY CHECK(id=1), last_success_at TEXT,
        last_backup_date TEXT, filename TEXT, last_error_at TEXT, last_error TEXT)''')


def worker_started(interval):
    from apps.api.main import db
    now = utc_now()
    with db() as con:
        worker_schema(con)
        con.execute('''INSERT INTO backup_worker(id,status,started_at,heartbeat_at,interval_seconds)
            VALUES(1,'idle',?,?,?) ON CONFLICT(id) DO UPDATE SET
            status='idle',started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,
            cycle_started_at=NULL,interval_seconds=excluded.interval_seconds''', (now, now, interval))


def mark_worker(status, error=None):
    from apps.api.main import db
    now = utc_now()
    with db() as con:
        worker_schema(con)
        con.execute('UPDATE backup_worker SET status=?,heartbeat_at=? WHERE id=1', (status, now))
        if status == 'running':
            con.execute('UPDATE backup_worker SET cycle_started_at=? WHERE id=1', (now,))
        if status == 'idle':
            con.execute('UPDATE backup_worker SET last_success_at=? WHERE id=1', (now,))
        if error:
            con.execute('UPDATE backup_worker SET last_error_at=?,last_error=? WHERE id=1', (now, error))


def worker_status():
    from apps.api.main import db
    with db() as con:
        worker_schema(con)
        row = con.execute('SELECT * FROM backup_worker WHERE id=1').fetchone()
    if not row:
        return {'status': 'not_started', 'heartbeat_at': None, 'last_success_at': None, 'last_error': None}
    result = dict(row)
    result.pop('id')
    result['stale_after_seconds'] = max(300, result['interval_seconds'] * 3)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(result['heartbeat_at'])).total_seconds()
    result['heartbeat_age_seconds'] = max(0, round(age))
    if result['status'] != 'stopped' and age > result['stale_after_seconds']:
        result['status'] = 'stale'
    return result


def record_backup(day=None, error=None):
    from apps.api.main import db
    now = utc_now()
    with db() as con:
        worker_schema(con)
        con.execute('INSERT OR IGNORE INTO backup_state(id) VALUES(1)')
        if error:
            con.execute('UPDATE backup_state SET last_error_at=?,last_error=? WHERE id=1', (now, error))
        else:
            con.execute('''UPDATE backup_state SET last_success_at=?,last_backup_date=?,filename=?,
                last_error=NULL WHERE id=1''', (now, day, f'accufolio-{day}.db'))


def backup_status():
    from apps.api.main import db
    with db() as con:
        worker_schema(con)
        row = con.execute('SELECT * FROM backup_state WHERE id=1').fetchone()
    result = dict(row) if row else dict.fromkeys(
        ('last_success_at', 'last_backup_date', 'filename', 'last_error_at', 'last_error'))
    result.pop('id', None)
    result['status'] = 'failed' if result['last_error'] else 'ok' if result['last_success_at'] else 'not_created'
    result['keep'] = max(1, int(os.environ.get('ACCUFOLIO_BACKUP_KEEP', '30')))
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


def run_worker(stop, interval):
    """Back up once per UTC day, retry failures, and never fetch market data."""
    last_backup = None
    initialized = False
    try:
        while not stop.is_set():
            try:
                if not initialized:
                    worker_started(interval)
                    initialized = True
                mark_worker('running')
                day = datetime.now(timezone.utc).date().isoformat()
                if last_backup != day:
                    try:
                        daily_backup(day)
                        record_backup(day)
                        last_backup = day
                    except Exception:
                        logging.exception('Accufolio automatic backup failed')
                        record_backup(error='自动备份未完成')
                        mark_worker('failed', '自动备份未完成')
                    else:
                        mark_worker('idle')
                else:
                    mark_worker('idle')
            except Exception:
                # If SQLite itself fails, health will expose the stale heartbeat
                # when it recovers; do not manufacture a successful cycle.
                logging.exception('Accufolio background maintenance failed')
            stop.wait(interval)
    finally:
        try:
            mark_worker('stopped')
        except Exception:
            logging.exception('Accufolio could not persist worker shutdown')
