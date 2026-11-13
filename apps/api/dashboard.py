"""Authenticated, read-only dashboard; all provider I/O occurs after SQLite closes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from apps.api.history import get_history
from packages.core.dashboard import build_dashboard, summarize_account
from packages.providers import get_quote

router = APIRouter()


def _authorize(credentials: HTTPBasicCredentials = Depends(HTTPBasic())):
    from apps.api.main import authorize
    return authorize(credentials)


@router.get('/api/dashboard')
def dashboard(period: Literal['1m', '3m', '1y', 'all'] = '1y', include_history: bool = True, actor: str = Depends(_authorize)):
    from apps.api.main import db
    today = date.today()
    with db() as con:
        trades = [dict(row) for row in con.execute('SELECT * FROM trades WHERE deleted=0 ORDER BY trade_date,id')]
    valid = [row for row in trades if row['trade_date'] <= today.isoformat()]
    first = date.fromisoformat(valid[0]['trade_date']) if valid else today
    earliest = max(date(1900, 1, 1), today - timedelta(days=30 * 366 - 8))
    start = max(first, earliest if period == 'all' else today - timedelta(days={'1m': 30, '3m': 90, '1y': 365}[period]))
    start = max(start, earliest)
    history_start = max(date(1900, 1, 1), start - timedelta(days=7))
    open_symbols = {row['symbol'] for row in summarize_account(trades, {}, today)['holdings']}
    before_window = summarize_account(valid, {}, start - timedelta(days=1))['holdings']
    symbols = sorted({row['symbol'] for row in before_window} |
                     {row['symbol'] for row in valid if row['trade_date'] >= start.isoformat()})
    if not include_history:
        symbols = sorted(open_symbols)
    quotes, histories, sources, warnings = {}, {}, [], []

    def fetch(symbol):
        quote = None
        if symbol in open_symbols:
            try:
                result = get_quote(symbol)
                quote = {**result.quote.__dict__, 'degraded': result.degraded} if result.quote else None
            except Exception:
                quote = None
        if not include_history:
            return symbol, quote, None
        try:
            history = get_history(symbol, start=history_start, end=today)
        except Exception:
            history = {'bars': [], 'source': 'sina', 'stale': False, 'cached': False,
                       'errors': ['日线暂不可用'], 'warnings': []}
        return symbol, quote, history

    if symbols:
        with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
            for symbol, quote, history in pool.map(fetch, symbols):
                if quote:
                    quotes[symbol] = quote
                    if quote.get('degraded'):
                        warnings.append(f'{symbol}：行情已降级，请核对数据来源')
                elif symbol in open_symbols:
                    warnings.append(f'{symbol}：当前行情暂不可用，整体估值暂不计算')
                if history is None:
                    continue
                histories[symbol] = history
                sources.append({'symbol': symbol, 'source': history.get('source', 'sina'),
                                'cached': history.get('cached', False), 'stale': history.get('stale', False),
                                'available_start': history.get('available_start'),
                                'available_end': history.get('available_end')})
                warnings.extend(f'{symbol}：{warning}' for warning in history.get('errors', []) + history.get('warnings', []))
    result = build_dashboard(trades, histories, quotes, start, today, include_history=include_history)
    result['period'] = period
    result['coverage']['stale_symbols'] = [source['symbol'] for source in sources if source['stale']]
    if result['coverage']['stale_symbols']:
        result['coverage']['complete'] = False
    result['sources'] = sources
    result['truncated'] = period == 'all' and first < earliest
    result['method'] = 'trade_reconstruction_unadjusted'
    result['method_note'] = '按买卖记录与未复权收盘价重建持仓市值；净投入为累计买入减累计卖出。不含账户现金、分红与手续费，不是账户净值。'
    if result['truncated']:
        warnings.append('曲线最多展示最近 30 年；此前交易仍计入持仓与净投入。')
    if result['summary']['excluded_future_trades']:
        warnings.append('未来日期的交易尚未计入看板。')
    result['warnings'] = list(dict.fromkeys(warnings))
    return result
