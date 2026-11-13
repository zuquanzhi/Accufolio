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
PLAN = {'securities': {'AAA': 50, 'BBB': 40, 'CCC': 10},
        'net_worth': {'account:1':50,'account:2':50},
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
                context=browser.new_context(viewport={'width':1280,'height':1000}, reduced_motion='reduce')
                assert context.request.post(base+'/api/session',data={'username':'admin','password':'dashboard-test','remember':True},headers={'X-Accufolio':'1'}).ok
                page=context.new_page();errors=[];writes=[];requests=[];pending=[]
                state={'missing_quote':False,'gap':False,'hold':False,'history_error':False,
                       'plan_status':200,'plan':deepcopy(PLAN),'missing_fx':False}
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
                def account_assets(route):
                    complete=not state['missing_fx'] and not state['missing_quote']
                    securities=None if state['missing_quote'] else 400
                    cash=None if state['missing_fx'] else 600
                    route.fulfill(json={'base':'USD','as_of':END.isoformat(),'net_assets_usd':1000 if complete else None,
                        'securities_value_usd':securities,'cash_value_usd':cash,'complete':complete,
                        'missing_fx':['HKD'] if state['missing_fx'] else [],'warnings':[],
                        'fx':{'base':'USD','rates':{'USD':1,'HKD':7.8,'CNY':7.2,'EUR':.8},'source':'fixture','as_of':END.isoformat(),'available':True,'stale':False},
                        'accounts':[{'id':1,'name':'Securities','kind':'broker','purpose':'investing','currency':'USD','balance':0,'as_of':None,'cash_value_usd':0,'securities_value_usd':securities,'value_usd':securities},
                                    {'id':2,'name':'Savings','kind':'bank','purpose':'savings','currency':'HKD','balance':4680,'as_of':END.isoformat(),'cash_value_usd':cash,'securities_value_usd':0,'value_usd':cash}]})
                page.route('**/api/assets',account_assets)
                page.route('**/api/accounts/2/cash-entries',lambda route:route.fulfill(json=[{'id':1,'account_id':2,'entry_date':END.isoformat(),'kind':'balance_open','amount':4680,'currency':'HKD','note':'Test opening'}]))
                page.route('**/api/trades' ,lambda route:route.fulfill(json=TRADES))
                page.route('**/api/version',lambda route:route.fulfill(json={'version':'dashboard-fixture','release':'2.0.0'}))
                def history(route):
                    args=parse_qs(urlsplit(route.request.url).query);symbol=args.get('symbol',['AAA'])[0]
                    route.fulfill(json={**HISTORIES[symbol],'symbol':symbol})
                page.route('**/api/history?*',history)
                def quote(route):
                    symbol=parse_qs(urlsplit(route.request.url).query).get('symbol',['AAA'])[0]
                    route.fulfill(json={'quote':QUOTES[symbol],'degraded':False,'errors':[]})
                page.route('**/api/summary?*',quote)
                # These other pages must also remain deterministic when traversed.
                for endpoint,response in [('health',{'release':'2.0.0','database':'ok','last_scan':None}),
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

                page.goto(base+'/investments')
                expect(page.locator('#value')).to_have_text('$400.00')
                expect(page.locator('#holdings-list details.holding')).to_have_count(3)
                expect(page.locator('#shares')).to_have_text('6.0000')
                expect(page.locator('#pnl')).to_have_text('$72.00')
                expect(page.locator('#cost')).to_have_text('$328.00')
                expect(page.locator('#realized')).to_have_text('$10.00')
                expect(page.locator('#allocation-ring svg')).to_have_count(1)
                expect(page.locator('#portfolio-chart .chart-line')).not_to_have_count(0)
                expect(page.get_by_role('heading',name=re.compile('^投资轨迹'))).to_have_count(1)
                expect(page.locator('#portfolio-chart .financial-chart')).to_have_attribute('aria-label','历史走势')
                expect(allocation('AAA')).to_contain_text('60.0%')
                expect(allocation('BBB')).to_contain_text('37.5%')
                expect(allocation('CCC')).to_contain_text('2.5%')
                expect(allocation('AAA')).to_contain_text('目标 50%')
                expect(allocation('AAA')).to_contain_text('-$40.00')
                expect(allocation('BBB')).to_contain_text('+$10.00')
                expect(allocation('CCC')).to_contain_text('+$30.00')
                page.evaluate('window.__detailMarker=19')
                page.locator('[data-asset-detail="allocation"]').click()
                page.wait_for_url(base+'/investments?detail=allocation')
                expect(page.locator('#investments-overview')).to_be_hidden()
                expect(page.locator('#investments-allocation')).to_be_visible()
                assert page.evaluate('window.__detailMarker')==19
                page.locator('[data-basis="cost"]').click()
                expect(allocation('AAA')).to_contain_text('61.0%')
                page.locator('[data-basis="shares"]').click()
                expect(allocation('AAA')).to_contain_text('33.3%')
                expect(allocation('BBB')).to_contain_text('50.0%')
                page.locator('[data-basis="value"]').click()
                aaa_holding=page.locator('#holdings-list details.holding[data-symbol="AAA"]')
                expect(aaa_holding.locator('.holding-weight')).to_contain_text('60.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='市值占比')).to_contain_text('60.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='成本占比')).to_contain_text('61.0%')
                expect(aaa_holding.locator('.holding-detail div').filter(has_text='股数占比')).to_contain_text('33.3%')
                tab('assets')
                expect(page.locator('#assets-total')).to_have_text('$1,000.00')
                expect(page.locator('#assets-securities')).to_have_text('$400.00')
                expect(page.locator('#assets-cash')).to_have_text('$600.00')
                expect(page.locator('#cost')).to_have_count(0)
                expect(page.locator('#portfolio-chart')).to_have_count(0)
                expect(page.locator('#total-allocation-legend [data-account-id="1"]')).to_contain_text('40.0%')
                expect(page.locator('#total-allocation-legend [data-account-id="1"]')).to_contain_text('+$100.00')
                expect(page.locator('#total-allocation-legend [data-account-id="2"]')).to_contain_text('60.0%')
                expect(page.locator('#total-allocation-legend [data-account-id="2"]')).to_contain_text('-$100.00')
                page.locator('#asset-accounts [data-account-id="2"]').click()
                page.wait_for_url(base+'/assets?account=2')
                expect(page.locator('#account-native-balance')).to_contain_text('4,680.00')
                expect(page.locator('#account-cash-entries')).to_contain_text('Test opening')
                page.go_back();page.wait_for_url(base+'/assets')
                state['missing_fx']=True;refresh()
                expect(page.locator('#assets-total')).to_have_text('—')
                expect(page.locator('#assets-total-note')).to_contain_text('HKD')
                expect(page.locator('#assets-securities')).to_have_text('$400.00')
                state['missing_fx']=False;state['plan_status']=503;refresh()
                expect(page.locator('#assets-total')).to_have_text('$1,000.00')
                expect(page.locator('#assets-allocation-note')).to_contain_text('配置目标暂不可用')
                tab('investments')
                page.locator('[data-asset-detail="allocation"]').click()
                expect(allocation('AAA')).not_to_contain_text('目标')
                expect(page.locator('#value')).to_have_text('$400.00')
                state['plan_status']=200;state['missing_quote']=True
                refresh();expect(page.locator('#value')).to_have_text('—')
                expect(page.locator('#holdings-list')).to_contain_text('BBB')
                state['missing_quote']=False
                page.locator('#asset-detail-back').click()
                page.wait_for_url(base+'/investments')
                expect(page.locator('#investments-overview')).to_be_visible()
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

                # A late response from an earlier period must not overwrite the latest choice.
                state['hold']=True
                page.locator('[data-period="3m"]').click()
                for _ in range(50):
                    if pending:break
                    page.wait_for_timeout(50)
                assert pending, 'Expected an in-flight historical request'
                state['hold']=False
                page.locator('[data-period="1m"]').click()
                expect(page.locator('#value')).to_have_text('$400.00')
                page.wait_for_function("!document.querySelector('#view-investments').hasAttribute('aria-busy')")
                for route,result in pending:
                    result['summary']['value']=9999
                    route.fulfill(json=result)
                pending.clear();page.wait_for_timeout(100)
                expect(page.locator('#value')).to_have_text('$400.00')
                expect(page.locator('[data-period="1m"]')).to_have_attribute('aria-pressed','true')
                expect(page.locator('#holdings-list details.holding')).to_have_count(3)
                tab('records')
                expect(page.locator('#trades .trade-row')).to_have_count(4)
                expect(page.locator('#trades button')).to_have_count(0)
                expect(page.locator('#new-trade')).to_have_count(0)
                tab('settings');expect(page.locator('#ledger-mode')).to_have_count(0)
                tab('investments');expect(page.locator('#value')).to_have_text('$400.00')
                page.locator('[data-asset-detail="performance"]').click()
                page.wait_for_url(base+'/investments?detail=performance')
                expect(page.locator('#investments-performance')).to_be_visible()
                expect(page.locator('#investments-overview')).to_be_hidden()
                expect(page.locator('#realized')).to_have_text('$10.00')
                page.go_back();page.wait_for_url(base+'/investments')
                expect(page.locator('#investments-overview')).to_be_visible()
                assert not writes, 'Read-only dashboard sent a write request: '+str(writes)
                assert not errors, errors
                browser.close()
            print('Dashboard browser checks passed: multi-asset ratios and targets, separate net-assets and investments screens, missing-data gaps, keyboard/mobile charts, request races, read-only named accounts and cash entries and focused detail routes.')
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=='__main__':
    main()
