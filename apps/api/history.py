"""Authenticated daily history with persistent cache and explicit stale fallback."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize as _authorize
from packages.providers.history import normalize_symbol, sina_daily, validate_bars
from packages.core.market_time import et_today, market_clock

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
TTL_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 5 * 60
MAX_RANGE_DAYS = 30 * 366
_LOCKS = [threading.Lock() for _ in range(32)]
ADJUSTMENT_NOTE = '未复权收盘价；不包含分红再投资，拆股区间可能使回测失真。'


class HistoryUnavailable(Exception):
    """The provider failed and no usable cached history exists."""


@contextmanager
def _cache_db():
    path = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT / 'data' / 'accufolio.db')))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('''CREATE TABLE IF NOT EXISTS daily_history_cache(
            symbol TEXT PRIMARY KEY, payload TEXT, fetched_at REAL,
            failure_at REAL, last_error TEXT)''')
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _read(symbol):
    with _cache_db() as connection:
        row = connection.execute('SELECT * FROM daily_history_cache WHERE symbol=?', (symbol,)).fetchone()
    if row is None:
        return None
    cached = dict(row)
    try:
        cached['bars'] = validate_bars(json.loads(cached['payload'])) if cached['payload'] else None
        stamp = cached['fetched_at']
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp <= 0:
            cached['bars'] = None
    except (ValueError, TypeError, OverflowError):
        cached['bars'] = None
    return cached


def _available(cached, now):
    if not cached or not cached['bars'] or not 0 <= now-cached['fetched_at'] < TTL_SECONDS:
        return False
    completed = _closed_at(now)
    if _closed_at(cached['fetched_at']) < completed:
        return False  # A new close invalidates any pre-close cached daily row.
    if cached['bars'][-1]['day'] < completed:
        return now-cached['fetched_at'] < RETRY_SECONDS  # Publication may lag the close.
    return True


def _closed_at(timestamp):
    return market_clock(datetime.fromtimestamp(timestamp, timezone.utc))['last_completed_session']


def _all_history(symbol):
    # Locks coalesce concurrent requests. Network calls never hold a SQLite transaction.
    cached = _read(symbol)
    now = time.time()
    if _available(cached, now):
        return cached['bars'], cached['fetched_at'], True, False, []
    with _LOCKS[hash(symbol) % len(_LOCKS)]:
        cached = _read(symbol)
        now = time.time()
        if _available(cached, now):
            return cached['bars'], cached['fetched_at'], True, False, []
        if cached and cached['failure_at'] and now - cached['failure_at'] < RETRY_SECONDS:
            if cached['bars']:
                return cached['bars'], cached['fetched_at'], True, True, [cached['last_error']]
            raise HistoryUnavailable(cached['last_error'])
        try:
            bars = sina_daily(symbol)
            bars = validate_bars(bars)
        except Exception as exc:
            message = f'新浪日线暂不可用（{type(exc).__name__}），请稍后重试。'
            with _cache_db() as connection:
                connection.execute('''INSERT INTO daily_history_cache(symbol,failure_at,last_error) VALUES(?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET failure_at=excluded.failure_at,last_error=excluded.last_error''',
                    (symbol, now, message))
            if cached and cached['bars']:
                return cached['bars'], cached['fetched_at'], True, True, [message]
            raise HistoryUnavailable(message) from exc
        # Eligibility is fixed at request start: a pre-close fetch must not turn
        # its forming daily candle into a final close just because time passed.
        fetched_at = now
        with _cache_db() as connection:
            connection.execute('''INSERT INTO daily_history_cache(symbol,payload,fetched_at) VALUES(?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at,
                failure_at=NULL,last_error=NULL''', (symbol, json.dumps(bars, separators=(',', ':')), fetched_at))
        return bars, fetched_at, False, False, []


def _range(start, end):
    end = date.fromisoformat(end) if isinstance(end, str) else end
    end = end or et_today()
    start = date.fromisoformat(start) if isinstance(start, str) else start
    start = start or end - timedelta(days=365)
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError('日期格式应为 YYYY-MM-DD')
    if start > end:
        raise ValueError('开始日期不能晚于结束日期')
    if start.year < 1900 or end > et_today() + timedelta(days=1):
        raise ValueError('请选择有效的历史日期范围')
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError('单次历史范围最长为 30 年')
    return start, end


def get_history(symbol: str, start=None, end=None) -> dict:
    """Internal read API for backtesting/monitoring, independent of HTTP auth.

    Data is cached six hours. Expired bars may be returned after a provider
    failure but always carry stale=True and errors; callers must show this.
    """
    symbol = normalize_symbol(symbol)
    start, end = _range(start, end)
    all_bars, fetched_at, cached, stale, errors = _all_history(symbol)
    completed = _closed_at(time.time())
    eligible = min(completed, _closed_at(fetched_at))
    available = [bar for bar in all_bars if bar['day'] <= eligible]
    cutoff = min(end.isoformat(), eligible)
    bars = [bar for bar in available if start.isoformat() <= bar['day'] <= cutoff]
    warnings = [ADJUSTMENT_NOTE]
    if not bars:
        errors = errors + ['所选时间范围没有日线数据，请调整日期或标的。']
    latest = date.fromisoformat(available[-1]['day']) if available else None
    if latest is None or latest < min(end, date.fromisoformat(completed)) - timedelta(days=7):
        stale = True
        warnings.append(f'数据源最新可用日期为 {latest.isoformat() if latest else "未知"}，可能已停止更新。')
    if latest and latest.isoformat() < min(end.isoformat(), completed):
        warnings.append(f'最近已收盘交易日为 {completed}；数据源收盘价仅到 {latest.isoformat()}。')
    jumps = [bar['day'] for previous, bar in zip(bars, bars[1:]) if not .55 < bar['close'] / previous['close'] < 1.8]
    if jumps:
        warnings.append('发现较大单日价格跳变，可能涉及拆股或其他公司行动；请核对：' + ', '.join(jumps[:5]))
    return {
        'symbol': symbol, 'start': start.isoformat(), 'end': end.isoformat(), 'bars': bars,
        'timezone': 'America/New_York', 'last_completed_session': completed,
        'source': 'sina', 'cached': cached, 'stale': stale,
        'adjustment': 'unadjusted', 'adjustment_note': ADJUSTMENT_NOTE,
        'fetched_at': datetime.fromtimestamp(fetched_at, timezone.utc).isoformat(),
        'available_start': available[0]['day'] if available else None,
        'available_end': available[-1]['day'] if available else None,
        'errors': errors, 'warnings': warnings,
    }


@router.get('/api/history')
def history(symbol: str = 'QQQM', start: date | None = None, end: date | None = None, actor: str = Depends(_authorize)):
    try:
        return get_history(symbol, start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HistoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
