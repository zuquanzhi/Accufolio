"""Browser regression suite. Uses local Edge on Windows or installed Chromium on CI."""
import os
import json
from copy import deepcopy
from datetime import date
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.core.dashboard import build_dashboard

ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'browser-test','ACCUFOLIO_DB':str(Path(temp)/'db.sqlite')}
        process=subprocess.Popen([sys.executable,'-m','uvicorn','apps.api.main:app','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            base=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urlopen(base+'/manifest.webmanifest',timeout=1).close();break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                options={'headless':True}
                if os.name=='nt':options['channel']='msedge'
                browser=p.chromium.launch(**options)
                context=browser.new_context(viewport={'width':1280,'height':900})
                def login(target):
                    assert target.request.post(base+'/api/session',data={'username':'admin','password':'browser-test','remember':True},headers={'X-Accufolio':'1'}).ok
                login(context)
                # Provider-facing endpoints are mocked for every page/context; no real market traffic.
                quote_reply={'shares':0,'cost':0,'average_price':None,'position':None,'quote':None,'degraded':True,'errors':['test offline'],'xirr':None,'xirr_reason':'样本不足'}
                dashboard_reply=build_dashboard([],{}, {},date(2025,1,1),date(2025,3,3))
                allocation_reply={'securities':{},'net_worth':{},'revision':0,'updated_at':None}
                dashboard_requests=[]
                def dashboard_response(route):
                    dashboard_requests.append(route.request.url)
                    result=deepcopy(dashboard_reply)
                    if 'include_history=false' in route.request.url:result.update(history_loaded=False,series=[])
                    route.fulfill(json=result)
                context.route('**/api/dashboard?*',dashboard_response)
                context.route('**/api/allocation',lambda route:route.fulfill(json=allocation_reply))
                context.route('**/api/assets',lambda route:route.fulfill(json={'base':'USD','net_assets_usd':0,'securities_value_usd':0,'cash_value_usd':0,'complete':True,'missing_fx':[],'accounts':[],'warnings':[]}))
                context.route('**/api/summary?*',lambda route:route.fulfill(json=quote_reply))
                server_records=[{'id':1,'symbol':'AAPL','trade_date':'2025-01-02','shares':5,'price':100,'side':'buy','note':'Agent record'}]
                context.route('**/api/trades',lambda route:route.fulfill(json=server_records))
                page=context.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                versions=['test-v1']
                page.route('**/api/version',lambda route:route.fulfill(json={'version':versions[0],'release':'2.0.0'}))
                historical_bars=[{'day':'2025-01-02','close':100},{'day':'2025-02-03','close':50},{'day':'2025-03-03','close':100}]
                history_reply={'bars':historical_bars,'source':'Test history','cached':False,'stale':False,'adjustment':'unadjusted','adjustment_note':'未复权收盘价；不包含分红再投资。','errors':[]}
                history_status=[200]
                page.route('**/api/history?*',lambda route:route.fulfill(status=history_status[0],json=history_reply if history_status[0]==200 else {'detail':'行情来源暂不可用'}))
                retired_requests=[]
                page.on('request',lambda req:retired_requests.append(req.url) if any('/api/'+part in req.url for part in ['watchlist','alerts','daily','signals','notifications','scan']) else None)
                page.goto(base)
                def tab(name):
                    page.evaluate('window.__documentMarker=1')
                    page.locator(f"a[data-view='{name}']").click()
                    page.wait_for_url(base+'/'+name)
                    expect(page.locator('#view-'+name)).to_be_visible()
                    expect(page.locator('.app-view')).to_have_count(1)
                    assert page.evaluate('window.__documentMarker') == 1, 'Tabs must retain the live document'
                def discovery(tool):
                    page.evaluate('window.__documentMarker=1')
                    if page.locator('#discover-back').is_visible():
                        page.locator('#discover-back').click()
                        expect(page.locator('#discover-home')).to_be_visible()
                    page.locator(f'[data-discover-tool="{tool}"]').click()
                    page.wait_for_url(base+'/discover?tool='+tool)
                    expect(page.locator(f'[data-discover-panel="{tool}"]')).to_be_visible()
                    assert page.evaluate('window.__documentMarker')==1
                page.wait_for_url(base+'/assets')
                expect(page.locator('#assets-total')).to_have_text('$0.00')
                expect(page.locator('#value')).to_have_count(0)
                tab('investments')
                expect(page.locator('#view-investments')).to_be_visible()
                expect(page.locator('#view-records')).to_have_count(0)
                expect(page.locator('#value')).to_have_text('$0.00')
                page.wait_for_function("!document.querySelector('#view-investments').hasAttribute('aria-busy')")
                assert any('include_history=false' in url for url in dashboard_requests)
                assert any('include_history' not in url for url in dashboard_requests)
                tab('settings');expect(page.locator('#native-settings')).to_be_hidden()
                expect(page.locator('#ledger-mode')).to_have_count(0)
                expect(page.locator('#default-scope')).to_have_count(0)
                expect(page.locator('#migration')).to_have_count(0)
                tab('records')
                expect(page.locator('#trades')).to_contain_text('5 股')
                expect(page.locator('#trades')).to_contain_text('Agent record')
                for selector in ['#new-trade','#trade','#local-entry','.trade-actions']:expect(page.locator(selector)).to_have_count(0)
                tab('investments');expect(page.locator('#shares')).to_have_text('0.0000')
                page.reload();expect(page.locator('#shares')).to_have_text('0.0000')
                page.evaluate('scrollTo(0,200)');saved_scroll=page.evaluate('scrollY')
                tab('settings');tab('investments');assert abs(page.evaluate('scrollY')-saved_scroll)<5
                tab('records');expect(page.locator('#trades')).to_contain_text('5 股')
                # A failed request clears the old display while keeping asset editing unavailable.
                failed_trades=lambda route:route.fulfill(status=503,json={'detail':'offline'})
                page.route('**/api/trades',failed_trades)
                page.evaluate('invalidateData()')
                expect(page.locator('#trades')).to_contain_text('暂时无法读取记录')
                expect(page.locator('#trades')).not_to_contain_text('5 股')
                expect(page.locator('#trades button')).to_have_count(0)
                tab('investments');expect(page.locator('#shares')).to_have_text('—')
                page.unroute('**/api/trades',failed_trades)
                page.evaluate('invalidateData()')
                expect(page.locator('#value')).to_have_text('$0.00')
                # Discovery drafts survive a real navigation, reload and browser back.
                tab('discover');expect(page.locator('#discover-home')).to_be_visible()
                expect(page.locator('#view-discover form:visible')).to_have_count(0)
                expect(page.locator('[data-discover-tool]')).to_have_count(2)
                page.wait_for_timeout(200)
                assert not retired_requests,retired_requests
                discovery('backtest')
                page.go_back();expect(page.locator('#discover-home')).to_be_visible()
                page.go_forward();expect(page.locator('#backtest-panel')).to_be_visible()
                assert page.evaluate('window.accufolioBack()') is True
                expect(page.locator('#discover-home')).to_be_visible()
                assert page.evaluate('window.accufolioBack()') is False
                discovery('backtest')
                assert not retired_requests,retired_requests
                page.locator('#backtest [name=start]').fill('2025-01-01')
                page.locator('#backtest [name=end]').fill('2025-04-01')
                page.locator('#run-history').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                expect(page.locator('#history-status')).to_contain_text('Test history')
                expect(page.locator('#history-status')).to_contain_text('未复权')
                expect(page.locator('#history-method')).to_contain_text('不包含分红再投资')
                history_status[0]=503
                page.locator('#run-history').click()
                expect(page.locator('#backtest-error')).to_contain_text('手动数据')
                expect(page.locator('#backtest-result')).to_be_empty()
                history_status[0]=200;history_reply['bars']=[]
                page.locator('#run-history').click()
                expect(page.locator('#backtest-error')).to_contain_text('没有可用的历史价格')
                history_reply['bars']=historical_bars;history_reply['cached']=True;history_reply['stale']=True
                page.locator('#run-history').click()
                expect(page.locator('#history-status')).to_contain_text('过期缓存')
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                history_reply.update(cached=False,warnings=['数据源可能尚未更新最近的交易日'])
                page.locator('#run-history').click()
                expect(page.locator('#history-status')).to_contain_text('数据可能已过期')
                expect(page.locator('#history-status')).not_to_contain_text('获取失败')
                expect(page.locator('#history-method')).to_contain_text('数据源可能尚未更新')
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                pending_history=[]
                delayed_history=lambda route:pending_history.append(route)
                page.route('**/api/history?*',delayed_history)
                page.locator('#run-history').click()
                expect(page.locator('#backtest')).to_have_attribute('aria-busy','true')
                tab('investments');tab('discover');discovery('backtest')
                expect(page.locator('#backtest')).to_have_attribute('aria-busy','true')
                page.locator('#backtest [name=monthly]').fill('301')
                expect(page.locator('#run-history')).to_be_enabled()
                page.unroute('**/api/history?*',delayed_history)
                for route in pending_history:route.fulfill(json={**history_reply,'source':'Cancelled old request'})
                page.locator('#backtest [name=monthly]').fill('300')
                page.locator('#run-history').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                expect(page.locator('#history-status')).not_to_contain_text('Cancelled old request')
                page.locator('#backtest [name=start]').fill('2025-05-01')
                page.locator('#run-history').click()
                expect(page.locator('#backtest-error')).to_contain_text('开始日期')
                # Restore the default range so later auto-update tests can clear the full draft.
                page.evaluate("const f=document.querySelector('#backtest');const d=new Date();const format=x=>new Date(x.getTime()-x.getTimezoneOffset()*60000).toISOString().slice(0,10);f.elements.end.value=format(d);d.setFullYear(d.getFullYear()-1);f.elements.start.value=format(d);f.dispatchEvent(new Event('input',{bubbles:true}));")
                page.locator('#manual-data summary').click()
                page.locator('#backtest [name=bars]').fill('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                tab('investments');page.go_back();page.wait_for_url(base+'/discover?tool=backtest')
                expect(page.locator('#backtest [name=bars]')).to_have_value('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                page.locator('#run-manual').click();expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                for width in [320,390,430]:
                    page.set_viewport_size({'width':width,'height':844})
                    for view in ['assets','investments','records','discover','settings']:
                        tab(view)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{view} overflows at {width}'
                    tab('discover')
                    for tool in ['backtest','income']:
                        discovery(tool)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (tool,width)
                        page.locator('#discover-back').click();expect(page.locator('#discover-home')).to_be_visible()
                    for link in page.locator('a[data-view]').all():
                        box=link.bounding_box();assert box['width']>=44 and box['height']>=44,box
                # Prewarmed pages stay interactive offline, without document or API requests.
                for view in ['assets','investments','records','discover','settings']:tab(view)
                page.wait_for_timeout(200)
                requested=[]
                listener=lambda request:requested.append(request.url) if request.is_navigation_request() or '/api/' in request.url else None
                page.on('request',listener)
                context.set_offline(True)
                for view in ['assets','investments','records','discover','settings']:tab(view)
                page.go_back();page.wait_for_url(base+'/discover')
                expect(page.locator('#view-discover')).to_be_visible()
                assert not requested, requested
                context.set_offline(False);page.remove_listener('request',listener)
                # Pending headline/history requests never block tabs or leak notices into another page.
                slow_context=browser.new_context()
                login(slow_context)
                slow_context.route('**/api/allocation',lambda route:route.fulfill(json=allocation_reply))
                slow_context.route('**/api/summary?*',lambda route:route.fulfill(json=quote_reply))
                slow_context.route('**/api/trades',lambda route:route.fulfill(json=[]))
                slow_context.route('**/api/dashboard?*',dashboard_response)
                slow=slow_context.new_page();slow.on('pageerror',lambda error:errors.append(str(error)))
                held=[]
                def slow_headline(route):
                    if 'include_history=false' in route.request.url:held.append(route)
                    else:dashboard_response(route)
                slow.route('**/api/dashboard?*',slow_headline)
                slow.goto(base+'/investments');slow.wait_for_function('pageTemplates !== null')
                slow.evaluate('window.__slowMarker=42')
                slow.locator("a[data-view='records']").click()
                expect(slow.locator('#view-records')).to_be_visible(timeout=1000)
                assert slow.evaluate('window.__slowMarker')==42
                late=deepcopy(dashboard_reply);late['coverage']['quote_missing_symbols']=['delayed quote'];late['history_loaded']=False
                for route in held:route.fulfill(json=late)
                expect(slow.locator('#view-records')).to_be_visible();slow.wait_for_timeout(150)
                assert 'delayed quote' not in slow.locator('#notice').inner_text()
                slow.close()
                staged=slow_context.new_page();staged.on('pageerror',lambda error:errors.append(str(error)));history_held=[]
                def slow_dashboard_history(route):
                    if 'include_history=false' in route.request.url:dashboard_response(route)
                    else:history_held.append(route)
                staged.route('**/api/dashboard?*',slow_dashboard_history)
                staged.goto(base+'/investments');expect(staged.locator('#value')).to_have_text('$0.00')
                expect(staged.locator('#portfolio-chart')).to_contain_text('正在读取历史行情')
                staged.evaluate('window.__stagedMarker=17');staged.locator("a[data-view='settings']").click()
                expect(staged.locator('#view-settings')).to_be_visible(timeout=1000)
                assert staged.evaluate('window.__stagedMarker')==17
                for route in history_held:route.fulfill(json=dashboard_reply)
                expect(staged.locator('#view-settings')).to_be_visible();staged.close();slow_context.close()
                # Editable analysis drafts protect automatic updates.
                page.wait_for_function('appVersion !== null')
                versions[0]='test-v2';page.evaluate('checkVersion()');expect(page.locator('#notice')).to_contain_text('新版本')
                tab('discover');discovery('backtest');page.locator('#backtest [name=bars]').fill('');page.locator('#backtest [name=monthly]').fill('400')
                page.wait_for_function('appVersion !== null');versions[0]='test-v3';page.evaluate('checkVersion()');expect(page.locator('#notice')).to_contain_text('新版本')
                # Only analysis drafts protect updates; a removed watch draft cannot block one.
                page.evaluate("sessionStorage.setItem('accufolio.draft.watch.v1',JSON.stringify({values:{threshold:'3'}}))")
                tab('records');tab('discover');discovery('backtest')
                expect(page.locator('#backtest [name=monthly]')).to_have_value('400')
                page.reload();expect(page.locator('#backtest [name=monthly]')).to_have_value('400')
                page.locator('#backtest [name=monthly]').fill('300')
                assert not page.evaluate('hasDrafts()'), 'No editable draft should remain'
                versions[0]='test-v4'
                with page.expect_navigation():page.evaluate('checkVersion()')
                tab('records');page.locator('#trades .trade-row').first.wait_for()
                page.goto(base+'/settings');page.wait_for_url(base+'/settings');expect(page.locator('#view-settings')).to_be_visible()
                # All routes can be opened independently without a preceding home visit.
                for view in ['assets','investments','records','discover','settings']:
                    page.goto(base+'/'+view);expect(page.locator('#view-'+view)).to_be_visible();expect(page.locator('.app-view')).to_have_count(1)
                for tool in ['backtest','income']:
                    page.goto(base+'/discover?tool='+tool)
                    expect(page.locator(f'[data-discover-panel="{tool}"]')).to_be_visible()
                    expect(page.locator('#discover-home')).to_be_hidden()
                    page.evaluate('window.__directDiscoverMarker=29')
                    page.locator('#discover-back').click()
                    expect(page.locator('#discover-home')).to_be_visible()
                    page.wait_for_url(base+'/discover')
                    assert page.evaluate('window.__directDiscoverMarker')==29
                # Bookmarks for retired tools return to discovery without obsolete API traffic.
                for tool in ['watchlist','alerts','notifications','daily']:
                    page.goto(base+'/discover?tool='+tool)
                    page.wait_for_url(base+'/discover');expect(page.locator('#discover-home')).to_be_visible()
                    expect(page.locator('[data-discover-tool]')).to_have_count(2)
                assert not retired_requests,retired_requests
                if os.environ.get('ACCUFOLIO_SCREENSHOT'):page.screenshot(path=os.environ['ACCUFOLIO_SCREENSHOT'],full_page=True)
                for version in ['1.3.0','2.0.0']:
                    native=browser.new_context(user_agent='Mozilla/5.0 AccufolioAndroid/'+version,viewport={'width':390,'height':844})
                    login(native)
                    np=native.new_page();np.goto(base+'/settings')
                    if version=='2.0.0':
                        expect(np.locator('#native-settings')).to_be_hidden()
                        for action in ['connection','update','reload']:
                            expect(np.locator('#native-'+action)).to_be_visible();expect(np.locator('#native-'+action)).to_have_attribute('href','accufolio://'+action)
                    else:expect(np.locator('#native-settings')).to_be_visible()
                    native.close()
                assert not errors, errors
                browser.close()
            print('Browser tests passed: separate assets/investments screens, read-only records, two-stage dashboard loading, stable-document routes/scroll, warmed offline navigation, delayed response isolation, automatic history backtests/cancellation, retired-tool removal, editable draft protection and version reload, mobile layouts and native settings links')
        finally:
            process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
