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
        env={**os.environ,'ACCUFOLIO_PASSWORD':'browser-test','ACCUFOLIO_DB':str(Path(temp)/'db.sqlite')}
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
                expect(page.locator('#local-entry')).to_be_hidden()
                page.select_option('#ledger-mode','local')
                page.locator('#trade [name=shares]').fill('2')
                page.locator('#trade [name=price]').fill('100')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('买入 2 股')
                page.locator('#trades').get_by_role('button',name='编辑').click()
                page.locator('#trade [name=shares]').fill('3')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('买入 3 股')
                page.locator('#trade [name=side]').select_option('sell')
                page.locator('#trade [name=shares]').fill('4')
                page.locator('#trade [name=price]').fill('120')
                page.locator('#save-trade').click()
                expect(page.locator('#notice')).to_contain_text('超过当时持仓')
                page.locator('#trade [name=shares]').fill('1')
                page.locator('#save-trade').click()
                expect(page.locator('#trades')).to_contain_text('卖出 1 股')
                expect(page.locator('#shares')).to_have_text('2.0000')
                page.reload()
                expect(page.locator('#trades')).to_contain_text('卖出 1 股')
                page.select_option('#ledger-mode','server')
                expect(page.locator('#local-entry')).to_be_hidden()
                expect(page.locator('#trades')).not_to_contain_text('卖出 1 股')
                page.select_option('#ledger-mode','local')
                page.locator('#backtest [name=bars]').fill('2025-01-02,100\n2025-02-03,50\n2025-03-03,100')
                page.locator('#backtest button').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                expect(page.locator('#backtest-result')).to_contain_text('50.00%')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows'
                # A new version must not destroy a partially filled form.
                page.route('**/api/version',lambda route:route.fulfill(json={'version':'changed-version','release':'1.0.0'}))
                page.locator('#trade [name=shares]').fill('4')
                page.evaluate('checkVersion()')
                expect(page.locator('#notice')).to_contain_text('发现新版本')
                expect(page.locator('#trade [name=shares]')).to_have_value('4')
                # Once clean, a new version is applied by reloading.
                page.locator('#trade [name=shares]').fill('')
                page.locator('#trade [name=price]').fill('')
                page.locator('#backtest [name=bars]').fill('')
                with page.expect_navigation():page.evaluate('checkVersion()')
                page.locator('#trades .trade-row').first.wait_for()
                if os.environ.get('ACCUFOLIO_SCREENSHOT'):
                    page.screenshot(path=os.environ['ACCUFOLIO_SCREENSHOT'],full_page=True)
                assert not errors, errors
                browser.close()
            print('Browser tests passed: local buy/sell, oversell protection, persistence, server separation, backtest, mobile layout and safe auto-update')
        finally:
            process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
