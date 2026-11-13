"""Dashboard browser regression using an isolated app and deterministic market data.

Run with the project virtualenv plus Playwright. All data endpoints are mocked;
no trade is written through HTTP, no local ledger is uploaded, and no provider
or notification endpoint is contacted.
"""
from copy import deepcopy
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.core.dashboard import build_dashboard

END = date(2026, 11, 13)
TRADES = [
    {'id': 1, 'symbol': 'AAA', 'trade_date': '2026-01-02', 'shares': 3, 'price': 100, 'side': 'buy', 'note': ''},
    {'id': 2, 'symbol': 'BBB', 'trade_date': '2026-01-05', 'shares': 3, 'price': 40, 'side': 'buy', 'note': ''},
    {'id': 3, 'symbol': 'CCC', 'trade_date': '2026-01-06', 'shares': 1, 'price': 8, 'side': 'buy', 'note': ''},
    {'id': 4, 'symbol': 'AAA', 'trade_date': '2026-03-02', 'shares': 1, 'price': 110, 'side': 'sell', 'note': ''},
]
QUOTES = {symbol: {'symbol': symbol, 'price': price, 'change_percent': 1, 'source': 'fixture', 'timestamp': 0}
          for symbol, price in [('AAA', 120), ('BBB', 50), ('CCC', 10)]}
HISTORIES = {symbol: {'bars': [{'day': (date(2026, 1, 1) + timedelta(days=i)).isoformat(), 'close': quote['price']}
                              for i in range((END-date(2026, 1, 1)).days+1)],
                       'source': 'fixture', 'cached': True, 'stale': False, 'errors': [], 'warnings': [],
                       'adjustment': 'unadjusted', 'adjustment_note': '测试日线 · 未复权'}
             for symbol, quote in QUOTES.items()}
PLAN = {'reserve_amount': 600, 'reserve_as_of': '2026-11-13',
        'investment_targets': {'AAA': 50, 'BBB': 40, 'CCC': 10},
        'total_targets': {'AAA': 30, 'BBB': 20, 'CCC': 10, '__RESERVE__': 40},
        'revision': 1, 'updated_at': '2026-11-13T00:00:00Z', 'note': 'fixture'}


def payload(period='1y', include_history=True, missing_quote=False, gap=False):
    start = max(date(2026, 1, 2), END-timedelta(days={'1m': 30, '3m': 90, '1y': 365, 'all': 10000}[period]))
    histories = deepcopy(HISTORIES)
    if gap:
        histories['AAA']['bars'] = [bar for bar in histories['AAA']['bars'] if not '2026-01-15' <= bar['day'] <= '2026-01-26']
    quotes = deepcopy(QUOTES)
    if missing_quote:
        quotes.pop('BBB')
    result = build_dashboard(TRADES, histories, quotes, start, END, include_history=include_history)
    result.update(period=period, sources=[], warnings=[], truncated=False,
                  method='trade_reconstruction_unadjusted', method_note='按真实买卖记录与未复权收盘价重建，不是账户净值。')
    return result


def main():
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ, 'ACCUFOLIO_USER':'admin', 'ACCUFOLIO_PASSWORD':'dashboard-test',
             'ACCUFOLIO_DB':str(Path(temp)/'dashboard.db'), 'ACCUFOLIO_WEBHOOK_URL':''}
        process=subprocess.Popen([sys.executable,'-m','uvicorn','apps.api.main:app','--port',str(port),
                                  '--timeout-graceful-shutdown','3'],cwd=ROOT,env=env,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            base=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urlopen(base+'/manifest.webmanifest',timeout=1).close();break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Test server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True, **({'channel':'msedge'} if os.name=='nt' else {}))
                context=browser.new_context(http_credentials={'username':'admin','password':'dashboard-test'},
                                            viewport={'width':1280,'height':1000}, reduced_motion='reduce')
                page=context.new_page();errors=[];writes=[];requests=[];pending=[]
                state={'missing_quote':False,'gap':False,'hold':False,'history_error':False,
                       'plan_status':200,'plan':deepcopy(PLAN)}
                page.on('pageerror',lambda error:errors.append(str(error)))
                def requested(request):
                    requests.append({'url':request.url,'body':request.post_data or ''})
                    if request.method not in ('GET','HEAD'):
                        writes.append(request.url)
                page.on('request',requested)
                def dashboard(route):
                    args=parse_qs(urlsplit(route.request.url).query)
                    period=args.get('period',['1y'])[0]
                    full=args.get('include_history',['true'])[0]!='false'
                    if state['history_error'] and full:
                        route.fulfill(status=503,json={'detail':'History unavailable'});return
                    result=payload(period,full,state['missing_quote'],state['gap'])
                    if state['hold'] and full:
                        pending.append((route,result));return
                    route.fulfill(json=result)
                page.route('**/api/dashboard*',dashboard)
                page.route('**/api/allocation',lambda route:route.fulfill(status=state['plan_status'],
                           json=state['plan'] if state['plan_status']==200 else {'detail':'Allocation unavailable'}))
                page.route('**/api/trades',lambda route:route.fulfill(json=TRADES))
                page.route('**/api/version',lambda route:route.fulfill(json={'version':'dashboard-fixture','release':'1.5.0'}))
                def history(route):
                    args=parse_qs(urlsplit(route.request.url).query);symbol=args.get('symbol',['AAA'])[0]
                    route.fulfill(json={**HISTORIES[symbol],'symbol':symbol})
                page.route('**/api/history?*',history)
                def quote(route):
                    symbol=parse_qs(urlsplit(route.request.url).query).get('symbol',['AAA'])[0]
                    route.fulfill(json={'quote':QUOTES[symbol],'degraded':False,'errors':[]})
                page.route('**/api/summary?*',quote)
                # These other pages must also remain deterministic when traversed.
                for endpoint,response in [('health',{'release':'1.5.0','database':'ok','last_scan':None}),
                                          ('audit',[]),('watchlist',[]),('signals',[]),('alerts',[]),('daily',[])]:
                    page.route('**/api/'+endpoint,lambda route,_request=None,reply=response:route.fulfill(json=reply))
                page.route('**/api/notifications/status',lambda route:route.fulfill(json={
                    'configured':False,'valid':True,'pending':0,'sending':0,'sent':0,'failed':0,'last_error':None}))

                def tab(name):
                    page.locator(f"a[data-view='{name}']").click()
                    page.wait_for_url(base+'/'+name)
                    expect(page.locator('#view-'+name)).to_be_visible()
                def refresh():
                    page.evaluate('invalidateData()')
                def allocation(symbol):
                    return page.locator('#allocation-legend [data-symbol="'+symbol+'"]')

                page.goto(base)
                expect(page.locator('#value')).to_have_text('$400.00')
                expect(page.locator('#holdings-list details.holding')).to_have_count(3)
                expect(page.locator('#shares')).to_have_text('6.0000')
                expect(page.locator('#pnl')).to_have_text('$82.00')
                expect(page.locator('#cost')).to_have_text('$328.00')
                expect(page.locator('#realized')).to_have_text('$10.00')
                expect(page.locator('#allocation-ring svg')).to_have_count(1)
                expect(page.locator('#portfolio-chart .chart-line')).not_to_have_count(0)
                expect(page.get_by_role('heading',name='投资轨迹',exact=True)).to_have_count(1)
                expect(page.locator('#portfolio-chart .financial-chart')).to_have_attribute('aria-label','历史走势')
                expect(allocation('AAA')).to_contain_text('60.0%')
                expect(allocation('BBB')).to_contain_text('37.5%')
                expect(allocation('CCC')).to_contain_text('2.5%')
                expect(allocation('AAA')).to_contain_text('目标 50%')
                expect(allocation('AAA')).to_contain_text('-$40.00')
                expect(allocation('BBB')).to_contain_text('+$10.00')
                expect(allocation('CCC')).to_contain_text('+$30.00')
                page.locator('[data-basis="cost"]').click()
                expect(allocation('AAA')).to_contain_text('61.0%')
                page.locator('[data-basis="shares"]').click()
                expect(allocation('AAA')).to_contain_text('33.3%')
                expect(allocation('BBB')).to_contain_text('50.0%')
                page.locator('[data-basis="value"]').click()
                page.locator('[data-scope="total"]').click()
                expect(page.locator('#value')).to_have_text('$1,000.00')
                expect(allocation('AAA')).to_contain_text('24.0%')
                expect(allocation('__RESERVE__')).to_contain_text('60.0%')
                expect(allocation('AAA')).to_contain_text('目标 30%')
                expect(allocation('AAA')).to_contain_text('+$60.00')
                expect(allocation('BBB')).to_contain_text('+$50.00')
                expect(allocation('CCC')).to_contain_text('+$90.00')
                expect(allocation('__RESERVE__')).to_contain_text('-$200.00')
                aaa_holding=page.locator('#holdings-list details.holding[data-symbol="AAA"]')
                expect(aaa_holding.locator('.holding-weight')).to_contain_text('24.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='组合内市值占比')).to_contain_text('60.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='组合内成本占比')).to_contain_text('61.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='组合内股数占比')).to_contain_text('33.3%')

                # A failed configuration read is different from a genuinely unconfigured balance.
                state['plan_status']=503
                refresh()
                expect(page.locator('#value')).to_have_text('—')
                expect(page.locator('#valuation-note')).to_contain_text('资金配置暂不可用')
                expect(page.locator('#allocation-ring')).to_contain_text('资金配置暂不可用')
                expect(page.locator('#allocation-source')).to_contain_text('资金配置暂不可用')
                expect(page.locator('#valuation-note')).not_to_contain_text('等待 Agent')
                page.locator('[data-scope="investments"]').click()
                expect(page.locator('#value')).to_have_text('$400.00')
                expect(allocation('AAA')).not_to_contain_text('目标')
                page.locator('[data-scope="total"]').click()

                # Missing other-funds balance means unknown total, not zero cash.
                state['plan_status']=200
                state['plan']={**PLAN,'reserve_amount':None,'reserve_as_of':None,
                               'investment_targets':{},'total_targets':{},'revision':0,'updated_at':None}
                refresh();expect(page.locator('#value')).to_have_text('—')
                expect(page.locator('#valuation-note')).to_contain_text('等待 Agent')
                expect(page.locator('#valuation-note')).not_to_contain_text('暂不可用')
                expect(page.locator('#allocation-source')).to_contain_text('尚未提供资金与目标配置')
                state['plan']=deepcopy(PLAN)
                state['missing_quote']=True
                refresh();expect(page.locator('#value')).to_have_text('—')
                page.locator('[data-scope="investments"]').click()
                expect(page.locator('#value')).to_have_text('—')
                expect(page.locator('#holdings-list')).to_contain_text('BBB')
                state['missing_quote']=False
                state['history_error']=True
                refresh();expect(page.locator('#portfolio-chart')).to_contain_text('历史曲线暂不可用')
                expect(page.locator('#value')).to_have_text('$400.00')
                state['history_error']=False
                state['gap']=True
                refresh();expect(page.locator('#value')).to_have_text('$400.00')
                page.locator('[data-period="all"]').click()
                page.wait_for_function("[...document.querySelectorAll('#portfolio-chart .chart-line')].some(p=>(p.getAttribute('d').match(/M/g)||[]).length>1)")
                # The chart is keyboard-readable and gaps do not become connected fake returns.
                plot=page.locator('#portfolio-chart .chart-plot');plot.focus();plot.press('Home')
                expect(page.locator('#portfolio-chart .chart-date')).to_have_text('2026-01-02')
                plot.press('End');expect(page.locator('#portfolio-chart .chart-date')).to_have_text(END.isoformat())
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Mobile horizontal overflow'
                page.set_viewport_size({'width':320,'height':740})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Small-phone horizontal overflow'
                page.set_viewport_size({'width':390,'height':844})

                # A late server response must not overwrite the device-local read-only archive.
                state['hold']=True
                page.locator('[data-period="3m"]').click()
                for _ in range(50):
                    if pending:break
                    page.wait_for_timeout(50)
                assert pending, 'Expected an in-flight historical request'
                local=[{'id':'local-one','symbol':'AAA','trade_date':'2026-01-02','shares':2,'price':90,
                        'side':'buy','note':'DEVICE_ONLY_SECRET'}]
                page.evaluate("rows=>localStorage.setItem('accufolio.local-ledger.v1',JSON.stringify(rows))",local)
                tab('settings');page.select_option('#ledger-mode','local');tab('assets')
                expect(page.locator('#value')).to_have_text('$240.00')
                for route,result in pending:
                    result['summary']['value']=9999
                    route.fulfill(json=result)
                pending.clear();state['hold']=False
                page.wait_for_timeout(100)
                expect(page.locator('#value')).to_have_text('$240.00')
                expect(page.locator('#holdings-list details.holding')).to_have_count(1)
                tab('records')
                expect(page.locator('#trades')).to_contain_text('DEVICE_ONLY_SECRET')
                expect(page.locator('#trades button')).to_have_count(0)
                if page.locator('#new-trade').count():expect(page.locator('#new-trade')).to_be_hidden()
                tab('settings');page.select_option('#ledger-mode','server');tab('assets')
                expect(page.locator('#value')).to_have_text('$400.00')
                expect(page.locator('#holdings-list details.holding')).to_have_count(3)
                assert not writes, 'Read-only dashboard sent a write request: '+str(writes)
                assert all('DEVICE_ONLY_SECRET' not in item['body'] and 'DEVICE_ONLY_SECRET' not in item['url'] for item in requests)
                assert not errors, errors
                browser.close()
            print('Dashboard browser checks passed: multi-asset ratios and targets, total/investment scopes, missing-data gaps, keyboard/mobile charts, request races, read-only local isolation.')
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=='__main__':
    main()
