"""Browser regression suite. Uses local Edge on Windows or installed Chromium on CI."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'browser-test','ACCUFOLIO_DB':str(Path(temp)/'db.sqlite'),'ACCUFOLIO_WEBHOOK_URL':''}
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
                context=browser.new_context(http_credentials={'username':'admin','password':'browser-test'},viewport={'width':1280,'height':900})
                page=context.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/summary?*',lambda route:route.fulfill(json={'shares':0,'cost':0,'average_price':None,'position':None,'quote':None,'degraded':True,'errors':['test offline'],'xirr':None,'xirr_reason':'样本不足'}))
                versions=['test-v1']
                page.route('**/api/version',lambda route:route.fulfill(json={'version':versions[0],'release':'1.4.0'}))
                historical_bars=[{'day':'2025-01-02','close':100},{'day':'2025-02-03','close':50},{'day':'2025-03-03','close':100}]
                history_reply={'bars':historical_bars,'source':'Test history','cached':False,'stale':False,'adjustment':'unadjusted','adjustment_note':'未复权收盘价；不包含分红再投资。','errors':[]}
                history_status=[200]
                page.route('**/api/history?*',lambda route:route.fulfill(status=history_status[0],json=history_reply if history_status[0]==200 else {'detail':'行情来源暂不可用'}))
                watch_reply=[{'symbol':symbol,'threshold':2} for symbol in ['QQQM','SPY','TLT']]
                signal_reply=[]
                notification_reply={'configured':False,'valid':True,'pending':0,'sending':0,'sent':0,'failed':0,'last_error':None}
                notification_status=[200];retry_status=[200];scan_requests=[];retry_requests=[]
                page.route('**/api/watchlist',lambda route:route.fulfill(json=watch_reply))
                page.route('**/api/signals',lambda route:route.fulfill(json=signal_reply))
                page.route('**/api/notifications/status',lambda route:route.fulfill(status=notification_status[0],json=notification_reply))
                def fake_scan(route):
                    assert route.request.method=='POST' and route.request.headers.get('x-accufolio')=='1'
                    scan_requests.append(route.request.url);route.fulfill(json=signal_reply)
                def fake_retry(route):
                    assert route.request.method=='POST' and route.request.headers.get('x-accufolio')=='1'
                    retry_requests.append(route.request.url)
                    if retry_status[0]!=200:
                        route.fulfill(status=retry_status[0],json={'detail':'https://notify.example/secret?token=DO_NOT_DISPLAY'});return
                    count=notification_reply['failed']
                    notification_reply.update(sent=notification_reply['sent']+count,failed=0,last_error=None)
                    route.fulfill(json={'requeued':count,**notification_reply})
                page.route('**/api/scan',fake_scan)
                page.route('**/api/notifications/retry',fake_retry)
                page.goto(base)
                def tab(name):
                    page.evaluate('window.__documentMarker=1')
                    page.locator(f"a[data-view='{name}']").click()
                    page.wait_for_url(base+'/'+name)
                    expect(page.locator('#view-'+name)).to_be_visible()
                    expect(page.locator('.app-view')).to_have_count(1)
                    assert page.evaluate('window.__documentMarker') == 1, 'Tabs must retain the live document'
                page.wait_for_url(base+'/assets')
                expect(page.locator('#view-assets')).to_be_visible()
                expect(page.locator('#view-records')).to_have_count(0)
                tab('settings')
                expect(page.locator('#native-settings')).to_be_hidden()
                page.select_option('#ledger-mode','local')
                tab('records');page.locator('#new-trade').click()
                page.locator('#trade [name=shares]').fill('2')
                page.locator('#trade [name=price]').fill('100')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('2 股')
                page.locator('#trades').get_by_role('button',name='编辑').click()
                page.locator('#trade [name=shares]').fill('3')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('3 股')
                page.locator('#new-trade').click()
                page.locator('#trade [name=side]').select_option('sell')
                page.locator('#trade [name=shares]').fill('4')
                page.locator('#trade [name=price]').fill('120')
                page.locator('#save-trade').click()
                expect(page.locator('#local-entry')).to_contain_text('超过当时持仓')
                page.locator('#trade [name=shares]').fill('1');page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('1 股')
                tab('assets');expect(page.locator('#shares')).to_have_text('2.0000')
                page.reload();expect(page.locator('#shares')).to_have_text('2.0000')
                tab('records');expect(page.locator('#trades')).to_contain_text('1 股')
                # A failed account must never display the previous account's values or controls.
                page.route('**/api/trades',lambda route:route.fulfill(status=503,json={'detail':'offline'}))
                tab('settings');page.select_option('#ledger-mode','server')
                page.go_back();page.wait_for_url(base+'/records')
                expect(page.locator('#new-trade')).to_be_hidden()
                expect(page.locator('#trades')).not_to_contain_text('1 股')
                expect(page.locator('#trades button')).to_have_count(0)
                tab('assets');expect(page.locator('#shares')).to_have_text('—')
                page.unroute('**/api/trades')
                tab('settings');page.select_option('#ledger-mode','local')
                # Discovery drafts survive a real navigation, reload and browser back.
                tab('discover')
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
                tab('assets');tab('discover')
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
                tab('assets');page.go_back();page.wait_for_url(base+'/discover')
                expect(page.locator('#backtest [name=bars]')).to_have_value('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                page.locator('#run-manual').click();expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                page.locator("button[data-discover='monitoring']").click()
                expect(page.locator('#monitoring')).to_be_visible();expect(page.locator('#backtest-panel')).to_be_hidden()
                expect(page.locator('#notification-status')).to_contain_text('尚未开启')
                expect(page.locator('#notification-note')).to_contain_text('ACCUFOLIO_WEBHOOK_URL')
                expect(page.locator('#retry-notifications')).to_be_hidden()
                expect(page.locator('#watch-items')).to_contain_text('等待首次检查')
                signal_reply.extend([
                    {'symbol':'QQQM','movement_level':1,'calm':{'status':'clear','as_of':'2025-03-03'}},
                    {'symbol':'SPY','movement_level':2,'calm':{'status':'skipped','reason':'需要至少 20 个共同交易日'}},
                    {'symbol':'TLT','movement_level':3,'calm':{'status':'triggered','as_of':'2025-03-03'}},
                ])
                notification_reply.update(configured=True,sent=4)
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('已发送 4')
                for symbol,level in [('QQQM',1),('SPY',2),('TLT',3)]:expect(page.locator('#watch-items')).to_contain_text(f'{symbol} · L{level}')
                expect(page.locator('#watch-items')).to_contain_text('暂无退潮信号')
                expect(page.locator('#watch-items')).to_contain_text('需要至少 20 个共同交易日')
                expect(page.locator('#watch-items')).to_contain_text('退潮信号已触发')
                notification_reply.update(pending=2,last_error='https://notify.example/secret?token=DO_NOT_DISPLAY')
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('等待发送 2')
                expect(page.locator('#notification-note')).to_contain_text('自动重试')
                expect(page.locator('#retry-notifications')).to_be_hidden()
                notification_reply.update(pending=0,failed=2)
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('失败 2')
                expect(page.locator('#retry-notifications')).to_be_visible()
                expect(page.locator('#notification-delivery')).not_to_contain_text('DO_NOT_DISPLAY')
                expect(page.locator('#notification-delivery')).not_to_contain_text('notify.example')
                retry_status[0]=503
                page.locator('#retry-notifications').click()
                expect(page.locator('#notification-note')).to_contain_text('暂时无法重试')
                expect(page.locator('#notification-delivery')).not_to_contain_text('DO_NOT_DISPLAY')
                expect(page.locator('#retry-notifications')).to_be_enabled()
                retry_status[0]=200
                page.locator('#retry-notifications').click()
                expect(page.locator('#notification-status')).to_contain_text('已发送 6')
                expect(page.locator('#retry-notifications')).to_be_hidden()
                assert len(retry_requests)==2 and len(scan_requests)==3
                notification_reply.update(valid=False,last_error='https://notify.example/secret?token=DO_NOT_DISPLAY')
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('配置需要检查')
                expect(page.locator('#notification-delivery')).not_to_contain_text('DO_NOT_DISPLAY')
                expect(page.locator('#retry-notifications')).to_be_hidden()
                notification_status[0]=503
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('暂时无法读取')
                expect(page.locator('#watch-items')).to_contain_text('QQQM · L1')
                notification_status[0]=200;notification_reply.update(valid=True,last_error=None)
                page.locator('#scan-now').click()
                expect(page.locator('#notification-status')).to_contain_text('已发送 6')
                for width in [320,390,430]:
                    page.set_viewport_size({'width':width,'height':844})
                    for view in ['assets','records','discover','settings']:
                        tab(view)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{view} overflows at {width}'
                    for link in page.locator('a[data-view]').all():
                        box=link.bounding_box();assert box['width']>=44 and box['height']>=44,box
                # Prewarmed pages stay interactive offline, without document or API requests.
                for view in ['assets','records','discover','settings']:tab(view)
                page.wait_for_timeout(200)
                requested=[]
                listener=lambda request:requested.append(request.url) if request.is_navigation_request() or '/api/' in request.url else None
                page.on('request',listener)
                context.set_offline(True)
                for view in ['assets','records','discover','settings']:tab(view)
                page.go_back();page.wait_for_url(base+'/discover')
                expect(page.locator('#view-discover')).to_be_visible()
                assert not requested, requested
                context.set_offline(False);page.remove_listener('request',listener)
                # A pending quote never blocks leaving the overview, and its late response is isolated.
                slow=context.new_page();slow.on('pageerror',lambda error:errors.append(str(error)))
                held=[];slow.route('**/api/summary?*',lambda route:held.append(route))
                slow.goto(base+'/assets');slow.wait_for_function('pageTemplates !== null')
                slow.evaluate('window.__slowMarker=42')
                slow.locator("a[data-view='records']").click()
                expect(slow.locator('#view-records')).to_be_visible(timeout=1000)
                assert slow.evaluate('window.__slowMarker')==42
                for route in held:route.fulfill(json={'shares':0,'cost':0,'average_price':None,'position':None,'quote':None,'degraded':True,'errors':['delayed quote'],'xirr':None})
                expect(slow.locator('#view-records')).to_be_visible();slow.wait_for_timeout(150)
                assert 'delayed quote' not in slow.locator('#notice').inner_text()
                slow.close()
                # Record sheet drafts belong to this tab and survive navigating away.
                tab('records');page.locator('#new-trade').click();page.locator('#trade [name=shares]').fill('4');page.locator('#close-trade').click()
                tab('settings');tab('records');page.locator('#new-trade').click()
                expect(page.locator('#trade [name=shares]')).to_have_value('4');page.locator('#close-trade').click()
                page.wait_for_function('appVersion !== null')
                versions[0]='test-v2';page.evaluate('checkVersion()');expect(page.locator('#notice')).to_contain_text('新版本')
                page.locator('#new-trade').click();page.locator('#trade [name=shares]').fill('');page.locator('#trade [name=side]').select_option('sell')
                assert page.evaluate("Boolean(readDraft('trade'))"), 'Direction-only draft must be protected'
                page.locator('#close-trade').click()
                tab('settings')
                page.once('dialog',lambda dialog:dialog.accept());page.select_option('#ledger-mode','server')
                page.select_option('#ledger-mode','local')
                page.go_back();page.wait_for_url(base+'/records');expect(page.locator('#new-trade')).to_be_visible()
                assert page.evaluate("readDraft('trade')") is None, 'Confirmed clear must survive browser back'
                tab('settings');page.select_option('#ledger-mode','local');tab('records');page.locator('#new-trade').click()
                expect(page.locator('#trade [name=side]')).to_have_value('buy');expect(page.locator('#trade [name=shares]')).to_have_value('');page.locator('#close-trade').click()
                tab('discover');page.locator("button[data-discover='backtest']").click();page.locator('#backtest [name=bars]').fill('');page.locator('#backtest [name=monthly]').fill('400')
                page.wait_for_function('appVersion !== null');versions[0]='test-v3';page.evaluate('checkVersion()');expect(page.locator('#notice')).to_contain_text('新版本')
                page.locator('#backtest [name=monthly]').fill('300')
                with page.expect_navigation():page.evaluate('checkVersion()')
                tab('records');page.locator('#trades .trade-row').first.wait_for()
                page.goto(base+'/#settings');page.wait_for_url(base+'/settings');expect(page.locator('#view-settings')).to_be_visible()
                # All routes can be opened independently without a preceding home visit.
                for view in ['assets','records','discover','settings']:
                    page.goto(base+'/'+view);expect(page.locator('#view-'+view)).to_be_visible();expect(page.locator('.app-view')).to_have_count(1)
                if os.environ.get('ACCUFOLIO_SCREENSHOT'):page.screenshot(path=os.environ['ACCUFOLIO_SCREENSHOT'],full_page=True)
                for version in ['1.3.0','1.4.0']:
                    native=browser.new_context(http_credentials={'username':'admin','password':'browser-test'},user_agent='Mozilla/5.0 AccufolioAndroid/'+version,viewport={'width':390,'height':844})
                    np=native.new_page();np.goto(base+'/settings')
                    if version=='1.4.0':
                        expect(np.locator('#native-settings')).to_be_hidden()
                        for action in ['connection','update','reload']:
                            expect(np.locator('#native-'+action)).to_be_visible();expect(np.locator('#native-'+action)).to_have_attribute('href','accufolio://'+action)
                    else:expect(np.locator('#native-settings')).to_be_visible()
                    native.close()
                assert not errors, errors
                browser.close()
            print('Browser tests passed: local buy/sell, oversell protection, persistence, account separation, stable-document routes, warmed offline navigation, delayed quote isolation, automatic history backtests and cancellation, manual fallback, signal levels, notification status and mocked retries, direct routes, back/reload drafts, mobile layouts and native settings links')
        finally:
            process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
