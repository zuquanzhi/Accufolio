"""Quote providers and explicit degradation; no silent stale-price fallback."""
from dataclasses import dataclass
from math import isfinite
from time import time
from typing import Callable
from urllib.request import Request, urlopen

@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    change_percent: float
    source: str
    timestamp: float

@dataclass(frozen=True)
class QuoteResult:
    quote: Quote | None
    degraded: bool
    errors: tuple[str, ...]

def sina_quote(symbol: str, fetch: Callable[[str], bytes] | None = None) -> Quote:
    symbol = symbol.strip().upper()
    if not symbol or not all(c.isascii() and (c.isalnum() or c in '.-') for c in symbol):
        raise ValueError('invalid symbol')
    url = 'https://hq.sinajs.cn/list=gb_' + symbol.lower()
    if fetch is None:
        def fetch(url):
            req = Request(url, headers={'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=12) as response:
                return response.read()
    raw = fetch(url).decode('gb18030')
    fields = raw.split('"')[1].split(',')
    price, change = float(fields[1]), float(fields[2])
    if not isfinite(price) or price <= 0 or not isfinite(change):
        raise ValueError('invalid provider values')
    return Quote(symbol, price, change, 'sina', time())

def get_quote(symbol: str, providers: tuple[Callable[[str], Quote], ...] = (sina_quote,)) -> QuoteResult:
    errors = []
    for provider in providers:
        try:
            quote = provider(symbol)
            if quote is None:
                raise ValueError('empty response')
            return QuoteResult(quote, bool(errors), tuple(errors))
        except Exception as exc:
            errors.append(f'{getattr(provider, "__name__", "provider")}: {type(exc).__name__}: {exc}')
    return QuoteResult(None, True, tuple(errors) or ('no providers configured',))
