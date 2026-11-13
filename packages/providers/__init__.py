"""Quote providers and explicit degradation; no silent stale-price fallback."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
import re
import ssl
from time import time
from typing import Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import certifi
from .history import normalize_symbol

MAX_RESPONSE_BYTES = 64 * 1024
MAX_QUOTE_AGE_SECONDS = 7 * 24 * 60 * 60

@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    change_percent: float
    source: str
    timestamp: float
    fetched_at: float | None = None

@dataclass(frozen=True)
class QuoteResult:
    quote: Quote | None
    degraded: bool
    errors: tuple[str, ...]

def _fetch_quote(url, fetch, referer):
    if fetch is None:
        def fetch(target):
            req = Request(target, headers={'Referer': referer, 'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=12, context=ssl.create_default_context(cafile=certifi.where())) as response:
                return response.read(MAX_RESPONSE_BYTES + 1)
    raw = fetch(url)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('quote response exceeds size limit')
    return raw.decode('gb18030')


def _validate_quote(quote, symbol):
    if not isinstance(quote, Quote) or quote.symbol != symbol:
        raise ValueError('quote symbol does not match request')
    if not isfinite(quote.price) or quote.price <= 0 or not isfinite(quote.change_percent):
        raise ValueError('invalid provider values')
    if not isfinite(quote.timestamp) or not -300 <= time() - quote.timestamp <= MAX_QUOTE_AGE_SECONDS:
        raise ValueError('quote timestamp is stale or in the future')
    return quote


def sina_quote(symbol: str, fetch: Callable[[str], bytes] | None = None) -> Quote:
    symbol = normalize_symbol(symbol)
    raw = _fetch_quote('https://hq.sinajs.cn/list=gb_' + symbol.lower(), fetch, 'https://finance.sina.com.cn')
    match = re.fullmatch(r'\s*var\s+hq_str_gb_' + re.escape(symbol.lower()) + r'="([^"\r\n]*)";?\s*', raw)
    if not match:
        raise ValueError('unexpected Sina quote response')
    fields = match.group(1).split(',')
    if len(fields) < 4:
        raise ValueError('incomplete Sina quote')
    # Sina field 3 is China time, not the API fetch time or US market time.
    timestamp = datetime.strptime(fields[3].strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
    return _validate_quote(Quote(symbol, float(fields[1]), float(fields[2]), 'sina', timestamp, time()), symbol)


def tencent_quote(symbol: str, fetch: Callable[[str], bytes] | None = None) -> Quote:
    """Tencent US quote fallback; field 30 is America/New_York local time.

    This public provider endpoint is unofficial and may supply delayed quotes.
    The response variable and currency are checked before consuming its fields.
    """
    symbol = normalize_symbol(symbol)
    raw = _fetch_quote('https://qt.gtimg.cn/q=us' + symbol, fetch, 'https://gu.qq.com')
    match = re.fullmatch(r'\s*v_us' + re.escape(symbol) + r'="([^"\r\n]*)";?\s*', raw)
    if not match:
        raise ValueError('unexpected Tencent quote response')
    fields = match.group(1).split('~')
    if len(fields) < 36 or fields[35] != 'USD':
        raise ValueError('incomplete or non-USD Tencent quote')
    timestamp = datetime.strptime(fields[30].strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
    price = float(fields[3])
    if fields[32]:
        change = float(fields[32])
    else:
        previous = float(fields[4])
        if not isfinite(previous) or previous <= 0:
            raise ValueError('invalid Tencent previous close')
        change = (price / previous - 1) * 100
    return _validate_quote(Quote(symbol, price, change, 'tencent', timestamp, time()), symbol)

def get_quote(symbol: str, providers: tuple[Callable[[str], Quote], ...] | None = None) -> QuoteResult:
    # Resolve defaults at call time, including during tests and provider changes.
    providers = (sina_quote, tencent_quote) if providers is None else providers
    errors = []
    try:
        symbol = normalize_symbol(symbol)
    except ValueError as exc:
        return QuoteResult(None, True, (str(exc),))
    for provider in providers:
        try:
            quote = _validate_quote(provider(symbol), symbol)
            return QuoteResult(quote, bool(errors), tuple(errors))
        except Exception as exc:
            errors.append(f'{getattr(provider, "__name__", "provider")}: {type(exc).__name__}: {exc}')
    return QuoteResult(None, True, tuple(errors) or ('no providers configured',))

# Daily history uses a separately tested, bounded JSON parser.
from .history import sina_daily
