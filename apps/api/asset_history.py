"""Read-only, all-account historical value and cash-flow-adjusted performance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize
from apps.api.history import get_history
from packages.core.market_time import market_clock
from packages.core.dashboard import summarize_account

router = APIRouter()
PERIODS = {'1m': 30, '3m': 90, '1y': 365}
CURRENCIES = {'USD', 'CNY', 'HKD', 'EUR'}


@router.get('/api/assets/history')
def asset_history(period: Literal['1m','3m','1y','all'] = '1y',
                  currency: Literal['USD','CNY','HKD','EUR'] = 'USD', actor: str = Depends(authorize)):
    from apps.api.main import db
    from apps.api.settlements import ensure_schema
    from apps.api import fx as fx_api
    from packages.core.net_worth import build_net_worth
    if period not in {*PERIODS, 'all'} or currency not in CURRENCIES:
        raise HTTPException(422, 'Invalid history period or currency')
    clock = market_clock()
    end = date.fromisoformat(clock['last_completed_session'])
    with db() as con:
        ensure_schema(con)
        con.execute('BEGIN')
        accounts = [dict(row) for row in con.execute('SELECT * FROM accounts ORDER BY id')]
        trades = [dict(row) for row in con.execute('SELECT * FROM trades WHERE deleted=0 ORDER BY trade_date,id')]
        cash = [dict(row) for row in con.execute('SELECT * FROM cash_entries ORDER BY entry_date,id')]
        settlements = [dict(row) for row in con.execute('SELECT * FROM trade_settlements ORDER BY trade_id')]
    # Historical accounts include archived ones. Closed accounts must not erase
    # previous balances, distributions, or external flows from performance.
    days = [row['trade_date'] for row in trades if row['trade_date'] <= end.isoformat()]
    days += [row['entry_date'] for row in cash if row['entry_date'] <= end.isoformat()]
    first = date.fromisoformat(min(days)) if days else end
    floor = max(date(1900,1,1), end-timedelta(days=30*366-8))
    start = max(first, floor, end-timedelta(days=PERIODS[period]) if period in PERIODS else floor)
    history_start = max(date(1900,1,1), start-timedelta(days=7))
    opening = summarize_account(trades, {}, start-timedelta(days=1))['holdings']
    symbols = sorted({row['symbol'] for row in opening} | {
        row['symbol'] for row in trades if start.isoformat() <= row['trade_date'] <= end.isoformat()})
    histories, sources, warnings = {}, [], []

    def fetch(symbol):
        try:
            return symbol, get_history(symbol, start=history_start, end=end)
        except Exception:
            return symbol, {'bars': [], 'source':'sina', 'stale':False, 'cached':False,
                            'errors':['历史价格暂不可用'], 'warnings':[]}

    if symbols:
        with ThreadPoolExecutor(max_workers=min(4,len(symbols))) as pool:
            for symbol, history in pool.map(fetch,symbols):
                histories[symbol] = history
                sources.append({'symbol':symbol,'source':history.get('source'),'stale':history.get('stale',False),
                                'cached':history.get('cached',False),'available_start':history.get('available_start'),
                                'available_end':history.get('available_end')})
                warnings.extend(symbol+'：'+str(message) for message in history.get('errors',[])+history.get('warnings',[]))
    fx_days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    identity = [{'day':day.isoformat(),'rates':{'USD':1},'rate_dates':{'USD':day.isoformat()},'source':'identity'} for day in fx_days]
    fx = {'base':'USD','points':identity,'stale':False,'errors':[]}
    if currency != 'USD' or any(row['currency'] != 'USD' for row in cash):
        try:
            fx = fx_api.get_historical_fx(fx_days, base='USD')
        except Exception:
            fx = {**fx,'errors':['历史汇率暂不可用，缺失折算保持空值。'],'stale':True}
    result = build_net_worth(accounts, trades, cash, settlements, histories, fx['points'], start, end,
                             display_currency=currency)
    result.update(currency=currency,period=period,as_of=clock['date'],market=clock,
                  start=result['series'][0]['day'] if result['series'] else start.isoformat(),
                  end=next((point['day'] for point in reversed(result['series']) if point['value'] is not None),None),
                  requested_end=end.isoformat(),sources=sources,truncated=first<floor,
                  fx={'source':fx.get('source','identity'),'stale':fx.get('stale',False),'errors':fx.get('errors',[])})
    by_day = {point['day']:point for point in fx['points']}
    for point in result['series']:
        reference = by_day.get(point['day'],{})
        point['fx_dates'] = reference.get('rate_dates',{})
    result['coverage']['stale_symbols'] = [item['symbol'] for item in sources if item['stale']]
    if result['coverage']['stale_symbols'] or fx.get('stale'):
        result['coverage']['complete'] = False
    warnings.extend(fx.get('errors',[]))
    if result['truncated']:
        warnings.append('最多展示最近30年；此前记录仍决定期初现金与持仓。')
    if result['coverage']['unmatched_trade_ids']:
        warnings.append('有证券交易尚未关联实际结算流水；请让 Agent 核对后使用 set_trade_settlement 关联。')
    result['warnings'] = list(dict.fromkeys([*result.get('warnings',[]),*warnings]))
    result['method_note'] = ('按美东已收盘交易日重建所有已记录账户的现金、证券与已关联的待结算款；包含已归档账户的历史。'
        '历史金额和真实日期的入出金使用当时已发布的汇率。净值按区间首点归一为1，入金视为区间初、出金视为区间末，'
        'TWR为日频近似；内部转账与证券结算不是外部入出金，分红和利息按实际净到账计收益。'
        '缺少行情、汇率或结算关联时不补造连续收益；未到账分红、拆股与未记录的资产或负债不在计算内。')
    return result
