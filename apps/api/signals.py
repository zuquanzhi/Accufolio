"""Daily calm signals using aligned stock and Cboe VIX closes, never live guesses."""
import csv
import io
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from urllib.request import Request, urlopen

from packages.core import rules
from packages.core.market_time import et_today, market_clock

VIX_URL = 'https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv'
TTL_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 5 * 60
MAX_AGE_DAYS = 7
_VIX_LOCK = threading.Lock()


def fetch_vix():
    request = Request(VIX_URL, headers={'User-Agent': 'Accufolio/1'})
    with urlopen(request, timeout=12) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('VIX response exceeds limit')
    return parse_vix(raw)


def parse_vix(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or not {'DATE', 'CLOSE'}.issubset(reader.fieldnames):
        raise ValueError('VIX CSV is missing DATE or CLOSE')
    result = {}
    for row in reader:
        day = datetime.strptime(row['DATE'], '%m/%d/%Y').date().isoformat()
        close = float(row['CLOSE'])
        if not isfinite(close) or close <= 0 or day in result:
            raise ValueError('invalid or duplicate VIX daily close')
        result[day] = close
    if not result:
        raise ValueError('VIX CSV is empty')
    return [{'day': day, 'close': result[day]} for day in sorted(result)][-500:]


def _vix_row(db):
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS volatility_cache(
            name TEXT PRIMARY KEY, payload TEXT, fetched_at REAL,
            failure_at REAL)''')
        row = con.execute("SELECT * FROM volatility_cache WHERE name='VIX'").fetchone()
        return dict(row) if row else None


def get_vix_history():
    from apps.api.main import db
    # One source request per process; the persisted cache survives app restarts.
    with _VIX_LOCK:
        now = time.time()
        row = _vix_row(db)
        bars = None
        if row and row['payload']:
            try:
                bars = json.loads(row['payload'])
                _series(bars)
            except (ValueError, TypeError, KeyError):
                bars = None
        if bars and _recent(row['fetched_at'], now, TTL_SECONDS):
            return {'bars': bars, 'stale': False, 'source': 'cboe', 'cached': True}
        if row and _recent(row['failure_at'], now, RETRY_SECONDS):
            return {'bars': bars or [], 'stale': True, 'source': 'cboe', 'cached': bool(bars)}
        try:
            bars = fetch_vix()
            _series(bars)
        except Exception:
            with db() as con:
                con.execute('''INSERT INTO volatility_cache(name,failure_at) VALUES('VIX',?)
                    ON CONFLICT(name) DO UPDATE SET failure_at=excluded.failure_at''', (now,))
            return {'bars': bars or [], 'stale': True, 'source': 'cboe', 'cached': bool(bars)}
        with db() as con:
            con.execute('''INSERT INTO volatility_cache(name,payload,fetched_at) VALUES('VIX',?,?)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload,
                fetched_at=excluded.fetched_at,failure_at=NULL''', (json.dumps(bars), time.time()))
        return {'bars': bars, 'stale': False, 'source': 'cboe', 'cached': False}


def _series(bars):
    result = {}
    for bar in bars:
        day = date.fromisoformat(bar['day']).isoformat()
        close = float(bar['close'])
        if not isfinite(close) or close <= 0 or day in result:
            raise ValueError('invalid or duplicate daily close')
        result[day] = close
    if not result:
        raise ValueError('daily closes are empty')
    return result


def _recent(timestamp, now, ttl):
    try:
        value = float(timestamp)
        return isfinite(value) and 0 < value <= now and now - value < ttl
    except (TypeError, ValueError):
        return False


def evaluate_calm(symbol, today=None):
    """20 common dates; VIX retreats >=25% and stock remains >=8% below peak.

    The latest common close may lag either provider while it publishes. We
    report that exact date and skip once it is more than seven days old.
    """
    from apps.api.history import get_history
    today = today or date.fromisoformat(market_clock()['last_completed_session'])
    skipped = lambda reason: {'status': 'skipped', 'reason': reason}
    try:
        history = get_history(symbol, start=today - timedelta(days=100), end=today)
        if history.get('stale'):
            return skipped('标的日线已过期，等待数据源恢复')
        stock = _series(history['bars'])
    except Exception:
        return skipped('标的日线暂不可用')
    try:
        history = get_vix_history()
        if history.get('stale'):
            return skipped('VIX 日线暂不可用或已过期')
        vix = _series(history['bars'])
    except Exception:
        return skipped('VIX 日线暂不可用')
    common = sorted(day for day in stock.keys() & vix.keys() if day <= today.isoformat())
    if len(common) < 20:
        return skipped('需要至少 20 个共同交易日的标的与 VIX 收盘价')
    as_of = common[-1]
    if (today - date.fromisoformat(as_of)).days > MAX_AGE_DAYS:
        return skipped('最新共同交易日超过 7 天，暂不生成信号')
    window = common[-20:]
    closes = [stock[day] for day in window]
    if any(not .55 < current / previous < 1.8 for previous, current in zip(closes, closes[1:])):
        return skipped('近期价格出现较大跳变，需核对拆股或其他公司行动')
    vix_peak = max(vix[day] for day in window)
    triggered = rules.calm_signal(stock[as_of], closes, vix[as_of], vix_peak)
    return {'status': 'triggered' if triggered else 'clear', 'reason': None,
            'as_of': as_of, 'window': 20, 'window_start': window[0],
            'price': stock[as_of], 'price_peak': max(closes),
            'vix': vix[as_of], 'vix_peak': vix_peak,
            'sources': {'price': 'sina', 'vix': 'cboe'}}
