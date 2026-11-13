"""Shared display defaults. Devices may override them without changing records."""
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize
from packages.providers.fx import SUPPORTED

router = APIRouter()
DEFAULTS = {'display_currency': 'USD', 'market_timezone': 'America/New_York'}


def ensure_schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    for key, value in DEFAULTS.items():
        con.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, value))


def _read(con):
    rows = {row['key']: dict(row) for row in con.execute(
        "SELECT * FROM settings WHERE key IN ('display_currency','market_timezone')")}
    return {**{key: rows[key]['value'] for key in DEFAULTS},
            'revision': max(row['revision'] for row in rows.values()),
            'updated_at': max(row['updated_at'] for row in rows.values())}


def get_settings():
    from apps.api.main import db
    with db() as con:
        ensure_schema(con)
        return _read(con)


def set_settings(display_currency, market_timezone, expected_revision, idempotency_key, dry_run=True):
    """Full replacement of shared viewing defaults, audited and retry-safe."""
    from apps.api.main import db
    if display_currency not in SUPPORTED:
        raise ValueError('Unsupported display currency')
    if not isinstance(market_timezone, str) or len(market_timezone) > 80:
        raise ValueError('Invalid timezone')
    try:
        ZoneInfo(market_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Use an IANA timezone such as America/New_York') from None
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError('A nonnegative integer revision is required')
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ValueError('A nonblank idempotency key of at most 200 characters is required')
    values = {'display_currency': display_currency, 'market_timezone': market_timezone}
    payload = json.dumps(['set_settings', values, expected_revision], sort_keys=True)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        ensure_schema(con)
        previous = con.execute('SELECT * FROM requests WHERE key=?', (idempotency_key,)).fetchone()
        if previous:
            if previous['payload'] != payload:
                raise HTTPException(409, 'Idempotency key already used with different data')
            return json.loads(previous['result'])
        current = _read(con)
        if current['revision'] != expected_revision:
            raise HTTPException(409, 'Settings changed; read the latest revision first')
        if dry_run:
            return {'preview': {'before': current, 'after': values, 'expected_revision': expected_revision}}
        revision = expected_revision + 1
        stamp = datetime.now(timezone.utc).isoformat()
        for key, value in values.items():
            con.execute('UPDATE settings SET value=?,revision=?,updated_at=? WHERE key=?', (value, revision, stamp, key))
        result = {**values, 'revision': revision, 'updated_at': stamp}
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',
                    ('mcp', 'set_settings', json.dumps({'before': current, 'after': result}, ensure_ascii=False)))
        con.execute('INSERT INTO requests(key,payload,result) VALUES(?,?,?)',
                    (idempotency_key, payload, json.dumps(result)))
    return result


@router.get('/api/settings')
def shared_settings(actor: str = Depends(authorize)):
    return get_settings()
