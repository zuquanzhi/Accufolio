"""Authenticated display FX with durable cache and explicit stale/unavailable states."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize
from packages.providers.fx import SOURCE_PAGE, SUPPORTED, ecb_rates, ecb_history, validate_rates


router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
TTL_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 5 * 60
SOURCE_MAX_AGE_DAYS = 7
_LOCK = threading.Lock()
_HISTORY_LOCK = threading.Lock()
NOTE = '当前金额使用最新参考汇率；历史金额按当时已发布汇率折算，缺失保持空值。交易仍保留原币金额。'


def ensure_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS fx_rates(
        base TEXT NOT NULL, quote TEXT NOT NULL, rate_date TEXT NOT NULL,
        rate REAL NOT NULL CHECK(rate>0), source TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(base,quote,rate_date))''')
    connection.execute('CREATE INDEX IF NOT EXISTS fx_rates_date ON fx_rates(rate_date)')
    connection.execute('''CREATE TABLE IF NOT EXISTS fx_history_cache(
        id INTEGER PRIMARY KEY CHECK(id=1), fetched_at REAL,
        failure_at REAL,last_error TEXT)''')


@contextmanager
def _cache_db():
    path = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT / 'data' / 'accufolio.db')))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        connection.execute('''CREATE TABLE IF NOT EXISTS display_fx_cache(
            id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, fetched_at REAL,
            failure_at REAL, last_error TEXT)''')
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _read():
    with _cache_db() as connection:
        row = connection.execute('SELECT * FROM display_fx_cache WHERE id=1').fetchone()
    if row is None:
        return None
    cached = dict(row)
    cached['data'] = None
    try:
        stamp = cached['fetched_at']
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp <= 0 or stamp > time.time():
            return cached
        cached['data'] = validate_rates(json.loads(cached['payload']))
    except (ValueError, TypeError, KeyError, OverflowError):
        pass
    return cached


def _fresh(cached, now):
    return bool(cached and cached['data'] and 0 <= now - cached['fetched_at'] < TTL_SECONDS)


def _response(data, fetched_at=None, cached=False, stale=False, errors=None):
    if data:
        as_of = datetime.strptime(data['as_of'], '%Y-%m-%d').date()
        stale = stale or (datetime.now(timezone.utc).date() - as_of).days > SOURCE_MAX_AGE_DAYS
    return {
        **(data or {'base': 'USD', 'rates': {'USD': 1.0}, 'as_of': None}),
        'available': data is not None, 'cached': cached, 'stale': bool(stale),
        'source': 'ECB', 'source_url': SOURCE_PAGE,
        'fetched_at': datetime.fromtimestamp(fetched_at, timezone.utc).isoformat() if fetched_at else None,
        'errors': errors or [], 'note': NOTE,
    }


def get_fx():
    """USD remains available even when no foreign-currency reference rate exists.

    All supported rates come from the same ECB date. Cache/provider failures
    never synthesize FX values and never silently relabel USD as another currency.
    """
    cached = _read()
    now = time.time()
    if _fresh(cached, now):
        return _response(cached['data'], cached['fetched_at'], cached=True)
    with _LOCK:
        cached = _read()
        now = time.time()
        if _fresh(cached, now):
            return _response(cached['data'], cached['fetched_at'], cached=True)
        failure_at = cached and cached['failure_at']
        if isinstance(failure_at, (int, float)) and 0 <= now - failure_at < RETRY_SECONDS:
            return _response(cached['data'], cached['fetched_at'] if cached['data'] else None,
                             cached=bool(cached['data']), stale=bool(cached['data']), errors=[cached['last_error']])
        try:
            data = validate_rates(ecb_rates())
        except Exception as exc:
            message = f'汇率暂不可用（{type(exc).__name__}），请稍后重试。'
            with _cache_db() as connection:
                connection.execute('''INSERT INTO display_fx_cache(id,failure_at,last_error) VALUES(1,?,?)
                    ON CONFLICT(id) DO UPDATE SET failure_at=excluded.failure_at,last_error=excluded.last_error''',
                    (now, message))
            return _response(cached['data'] if cached else None,
                             cached['fetched_at'] if cached and cached['data'] else None,
                             cached=bool(cached and cached['data']), stale=bool(cached and cached['data']), errors=[message])
        fetched_at = time.time()
        with _cache_db() as connection:
            connection.execute('''INSERT INTO display_fx_cache(id,payload,fetched_at) VALUES(1,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at,
                failure_at=NULL,last_error=NULL''', (json.dumps(data, separators=(',', ':')), fetched_at))
            _store_snapshots(connection, [data], fetched_at)
        return _response(data, fetched_at)


@router.get('/api/fx')
def fx(actor: str = Depends(authorize)):
    return get_fx()


def _store_snapshots(connection, snapshots, fetched_at):
    stamp = datetime.fromtimestamp(fetched_at, timezone.utc).isoformat()
    rows = [(snapshot['base'], currency, snapshot['as_of'], rate, 'ECB', stamp)
            for snapshot in snapshots for currency, rate in snapshot['rates'].items() if currency != snapshot['base']]
    connection.executemany('''INSERT INTO fx_rates(base,quote,rate_date,rate,source,fetched_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(base,quote,rate_date) DO UPDATE SET
        rate=excluded.rate,source=excluded.source,fetched_at=excluded.fetched_at''', rows)


def _history_meta():
    with _cache_db() as con:
        row = con.execute('SELECT * FROM fx_history_cache WHERE id=1').fetchone()
    return dict(row) if row else {}


def _recent_timestamp(stamp, now, duration):
    return isinstance(stamp, (int, float)) and math.isfinite(stamp) and 0 <= now - stamp < duration


def _refresh_history():
    def status(meta, cached):
        return {'cached': cached, 'stale': bool(meta.get('last_error')),
                'errors': [meta['last_error']] if meta.get('last_error') else []}
    meta = _history_meta()
    if _recent_timestamp(meta.get('fetched_at'), time.time(), TTL_SECONDS):
        return status(meta, True)
    with _HISTORY_LOCK:
        meta, now = _history_meta(), time.time()
        if _recent_timestamp(meta.get('fetched_at'), now, TTL_SECONDS):
            return status(meta, True)
        if _recent_timestamp(meta.get('failure_at'), now, RETRY_SECONDS):
            return status(meta, bool(meta.get('fetched_at')))
        try:
            snapshots = ecb_history()
            # Validate patched/custom providers as well as the network parser.
            previous = None
            if not 1 <= len(snapshots) <= 15000:
                raise ValueError('Invalid history size')
            for snapshot in snapshots:
                day = date.fromisoformat(snapshot['as_of'])
                if (snapshot.get('base') != 'USD' or day.year < 1999 or day > datetime.now(timezone.utc).date()
                        or (previous is not None and day <= previous)):
                    raise ValueError('Invalid historical snapshot')
                previous = day
                rates = snapshot['rates']
                if rates.get('USD') != 1 or isinstance(rates.get('USD'), bool):
                    raise ValueError('Invalid USD identity rate')
                for currency, value in rates.items():
                    if (currency not in SUPPORTED or isinstance(value, bool) or not isinstance(value, (float, int))
                            or not math.isfinite(value) or value <= 0):
                        raise ValueError('Invalid historical rate')
        except Exception as exc:
            error = f'历史汇率暂不可用（{type(exc).__name__}），缺失日期不折算。'
            with _cache_db() as con:
                con.execute('''INSERT INTO fx_history_cache(id,failure_at,last_error) VALUES(1,?,?)
                    ON CONFLICT(id) DO UPDATE SET failure_at=excluded.failure_at,last_error=excluded.last_error''', (now, error))
            return {'cached': bool(meta.get('fetched_at')), 'stale': True, 'errors': [error]}
        stamp = time.time()
        with _cache_db() as con:
            _store_snapshots(con, snapshots, stamp)
            con.execute('''INSERT INTO fx_history_cache(id,fetched_at) VALUES(1,?)
                ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at,failure_at=NULL,last_error=NULL''', (stamp,))
        return {'cached': False, 'stale': False, 'errors': []}


def get_historical_fx(days, base='USD', refresh=True):
    """1 base = rate quote, using same-day or earlier published reference rates.

    Missing rates stay null. Carry is bounded to seven calendar days so an
    outage cannot silently stretch one snapshot across months. Cross rates
    always use inputs from the same publication date.
    """
    if base not in SUPPORTED:
        raise ValueError('Unsupported base currency')
    dates = sorted({date.fromisoformat(day) if isinstance(day, str) else day for day in days})
    if dates and (len(dates) > 11000 or dates[-1] > datetime.now(timezone.utc).date()
                  or (dates[-1]-dates[0]).days > 30 * 366):
        raise ValueError('Invalid historical FX range')
    meta = _refresh_history() if dates and refresh else {'cached': True, 'stale': False, 'errors': []}
    snapshots = {}
    if dates:
        with _cache_db() as con:
            rows = con.execute('SELECT * FROM fx_rates WHERE rate_date>=? AND rate_date<=? ORDER BY rate_date',
                               ((dates[0]-timedelta(days=7)).isoformat(), dates[-1].isoformat())).fetchall()
        for row in rows:
            if (row['base'] not in SUPPORTED or row['quote'] not in SUPPORTED
                    or not isinstance(row['rate'], (float, int)) or not math.isfinite(row['rate']) or row['rate'] <= 0):
                continue
            try:
                day = date.fromisoformat(row['rate_date'])
            except ValueError:
                continue
            snapshots.setdefault(day, {})[(row['base'], row['quote'])] = dict(row)
    known = sorted(snapshots)
    points = []
    for day in dates:
        point = {'day': day.isoformat(), 'rates': {currency: None for currency in SUPPORTED},
                 'rate_dates': {currency: None for currency in SUPPORTED},
                 'sources': {currency: None for currency in SUPPORTED},
                 'fetched_ats': {currency: None for currency in SUPPORTED}, 'source': 'ECB', 'fetched_at': None}
        point['rates'][base] = 1.0
        point['rate_dates'][base] = day.isoformat()
        point['sources'][base] = 'identity'
        index = bisect_right(known, day)-1
        for currency in SUPPORTED:
            if currency == base:
                continue
            cursor = index
            while cursor >= 0 and (day-known[cursor]).days <= 7:
                snapshot = snapshots[known[cursor]]
                def leg(a, b):
                    if a == b:
                        return 1.0, []
                    if (a, b) in snapshot:
                        row = snapshot[(a, b)]
                        return row['rate'], [row]
                    if (b, a) in snapshot:
                        row = snapshot[(b, a)]
                        return 1.0 / row['rate'], [row]
                    return None, []
                rate, used = leg(base, currency)
                if rate is None:
                    first, left = leg(base, 'USD')
                    second, right = leg('USD', currency)
                    if first is not None and second is not None:
                        rate, used = first * second, left + right
                if rate is not None and math.isfinite(rate) and rate > 0:
                    point['rates'][currency] = rate
                    point['rate_dates'][currency] = known[cursor].isoformat()
                    point['sources'][currency] = ' / '.join(sorted({row['source'] for row in used}))
                    stamps = [row['fetched_at'] for row in used]
                    point['fetched_ats'][currency] = max(stamps) if stamps else None
                    point['fetched_at'] = max([point['fetched_at'] or '', *stamps]) or None
                    break
                cursor -= 1
        sources = {value for value in point['sources'].values() if value and value != 'identity'}
        point['source'] = ' / '.join(sorted(sources)) if sources else None
        points.append(point)
    return {'base': base, 'points': points, 'source': 'ECB', 'source_url': SOURCE_PAGE,
            'method': 'published_on_or_before_date', 'maximum_carry_days': 7,
            'note': '历史折算使用当日或此前已发布的参考汇率，不使用未来或当前汇率填补缺口。', **meta}


@router.get('/api/fx/history')
def fx_history(start: date, end: date, base: str = 'USD', actor: str = Depends(authorize)):
    if start > end or (end-start).days > 30 * 366:
        raise HTTPException(422, 'Invalid historical FX range')
    try:
        return get_historical_fx([start+timedelta(days=i) for i in range((end-start).days+1)], base)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
