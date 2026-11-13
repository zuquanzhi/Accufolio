"""Sina's public US daily bars; prices are deliberately labelled unadjusted."""
from datetime import date
import json
import math
import re
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SYMBOL = re.compile(r'^[A-Z0-9][A-Z0-9.\-]{0,14}$')


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not SYMBOL.fullmatch(value):
        raise ValueError('标的代码须为 1–15 位英文字母、数字、点或连字符')
    return value


def validate_bars(rows) -> list[dict]:
    if not isinstance(rows, list) or not rows or len(rows) > 40000:
        raise ValueError('日线数据为空或超过支持范围')
    bars = []
    previous = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('日线数据格式异常')
        day = date.fromisoformat(str(row.get('day', row.get('d', ''))))
        value = row.get('close', row.get('c', 'nan'))
        if isinstance(value, bool):
            raise ValueError('日线收盘价必须为有限正数')
        close = float(value)
        if not math.isfinite(close) or close <= 0:
            raise ValueError('日线收盘价必须为有限正数')
        if day.year < 1900 or previous is not None and day <= previous:
            raise ValueError('日线日期必须严格递增且不能重复')
        bars.append({'day': day.isoformat(), 'close': close})
        previous = day
    return bars


def sina_daily(symbol: str, fetch=None) -> list[dict]:
    """Return all available unadjusted daily closes; no JS decoder or execution.

    The endpoint is Sina's own JSON service. It was live-verified with QQQM
    and AAPL. The service is unofficial and availability is not guaranteed.
    """
    symbol = normalize_symbol(symbol)
    url = 'https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK?symbol=' + symbol.lower()
    if fetch is None:
        def fetch(target):
            request = Request(target, headers={'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Accufolio/1.4 Mozilla/5.0'})
            with urlopen(request, timeout=15) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError('日线响应超过大小限制')
                return content
    raw = fetch(url)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('日线响应格式或大小不符合要求')
    return validate_bars(json.loads(raw.decode('utf-8-sig')))
