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
                context=browser.new_context(http_credentials={'username':'admin','password':'browser-test'},viewport={'width':1280,'height':900})
                page=context.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/summary?*',lambda route:route.fulfill(json={'shares':0,'cost':0,'average_price':None,'position':None,'quote':None,'degraded':True,'errors':['test offline'],'xirr':None,'xirr_reason':'样本不足'}))
                page.goto(base)
                def tab(name):
                    page.locator(f"button[data-view='{name}']").click()
                    expect(page.locator('#view-'+name)).to_be_visible()
                expect(page.locator('#view-assets')).to_be_visible()
                expect(page.locator('#view-records')).to_be_hidden()
                tab('settings')
                expect(page.locator('#native-settings')).to_be_hidden()
                page.select_option('#ledger-mode','local')
                tab('records')
                page.locator('#new-trade').click()
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
                expect(page.locator('#local-entry')).to_be_visible()
                expect(page.locator('#local-entry')).to_contain_text('超过当时持仓')
                page.locator('#trade [name=shares]').fill('1')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('1 股')
                tab('assets')
                expect(page.locator('#shares')).to_have_text('2.0000')
                page.reload()
                tab('records')
                expect(page.locator('#trades')).to_contain_text('1 股')
                page.route('**/api/trades',lambda route:route.fulfill(status=503,json={'detail':'offline'}))
                tab('settings')
                page.select_option('#ledger-mode','server')
                tab('assets')
                expect(page.locator('#shares')).to_have_text('—')
                expect(page.locator('#recent-trades')).not_to_contain_text('1 股')
                tab('records')
                expect(page.locator('#new-trade')).to_be_hidden()
                expect(page.locator('#trades')).not_to_contain_text('1 股')
                expect(page.locator('#trades button')).to_have_count(0)
                page.unroute('**/api/trades')
                tab('settings');page.select_option('#ledger-mode','local')
                tab('discover')
                page.locator('#backtest [name=bars]').fill('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                tab('assets');tab('discover')
                expect(page.locator('#backtest [name=bars]')).to_have_value('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                page.locator('#backtest button').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                page.locator("button[data-discover='monitoring']").click()
                expect(page.locator('#monitoring')).to_be_visible()
                expect(page.locator('#backtest-panel')).to_be_hidden()
                tab('assets')
                for width in [320,390,430]:
                    page.set_viewport_size({'width':width,'height':844})
                    for view in ['assets','records','discover','settings']:
                        tab(view)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{view} overflows at {width}'
                    for button in page.locator('button[data-view]').all():
                        box=button.bounding_box()
                        assert box['width']>=44 and box['height']>=44, box
                # Draft survives dismissing its sheet and switching tabs.
                tab('records');page.locator('#new-trade').click()
                page.locator('#trade [name=shares]').fill('4')
                page.locator('#close-trade').click()
                tab('settings');tab('records');page.locator('#new-trade').click()
                expect(page.locator('#trade [name=shares]')).to_have_value('4')
                # New versions must also protect drafts in closed sheets.
                page.locator('#close-trade').click()
                page.route('**/api/version',lambda route:route.fulfill(json={'version':'changed-version','release':'1.3.0'}))
                page.evaluate('checkVersion()')
                expect(page.locator('#notice')).to_contain_text('新版本')
                page.locator('#new-trade').click()
                expect(page.locator('#trade [name=shares]')).to_have_value('4')
                page.locator('#trade [name=shares]').fill('')
                page.locator('#trade [name=price]').fill('')
                page.locator('#trade [name=side]').select_option('sell')
                assert page.evaluate('tradeDirty()'), 'Direction-only draft must be protected'
                page.locator('#trade [name=side]').select_option('buy')
                page.locator('#close-trade').click()
                tab('discover');page.locator("button[data-discover='backtest']").click()
                page.locator('#backtest [name=bars]').fill('')
                page.locator('#backtest [name=monthly]').fill('400')
                page.evaluate('checkVersion()')
                expect(page.locator('#backtest [name=monthly]')).to_have_value('400')
                page.locator('#backtest [name=monthly]').fill('300')
                with page.expect_navigation():page.evaluate('checkVersion()')
                tab('records')
                page.locator('#trades .trade-row').first.wait_for()
                if os.environ.get('ACCUFOLIO_SCREENSHOT'):
                    page.screenshot(path=os.environ['ACCUFOLIO_SCREENSHOT'],full_page=True)
                native=browser.new_context(http_credentials={'username':'admin','password':'browser-test'},user_agent='Mozilla/5.0 AccufolioAndroid/1.3.0',viewport={'width':390,'height':844})
                np=native.new_page();np.goto(base)
                np.locator("button[data-view='settings']").click()
                expect(np.locator('#native-settings')).to_be_visible()
                expect(np.locator('#native-settings')).to_have_attribute('href','accufolio://settings')
                native.close()
                assert not errors, errors
                browser.close()
            print('Browser tests passed: local buy/sell, oversell protection, persistence, account separation, tabs, sheet drafts, backtest, 320–430 px layouts and safe auto-update')
        finally:
            process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
