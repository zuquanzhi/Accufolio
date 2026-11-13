"""Capture real UI with an isolated database and explicitly synthetic market data.

Run from the repository root with the application's dependencies and Playwright.
No production data, configuration, or provider endpoint is changed.
"""
import base64,json,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import Request,urlopen
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets'/'previews'
DEMO_SERVER='''
from datetime import date
import uvicorn
import apps.api.main as api
import apps.api.extended as monitoring
from packages.providers import Quote, QuoteResult
class PreviewDate(date):
    @classmethod
    def today(cls): return cls(2026,11,13)
api.date=PreviewDate
quote=lambda symbol: QuoteResult(Quote(symbol,204.75,0.84,'demo',1794528000),False,())
api.get_quote=quote
monitoring.get_quote=quote
uvicorn.run(api.app,host='127.0.0.1',port=int(__import__('os').environ['PREVIEW_PORT']),log_level='warning')
'''

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'preview-only','ACCUFOLIO_DB':str(Path(temp)/'demo.db'),'PREVIEW_PORT':str(port)}
        process=subprocess.Popen([sys.executable,'-c',DEMO_SERVER],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        base=f'http://127.0.0.1:{port}'
        def api(path,data,method='POST'):
            req=Request(base+path,data=json.dumps(data).encode(),method=method,headers={'Authorization':'Basic '+base64.b64encode(b'admin:preview-only').decode(),'Content-Type':'application/json','X-Accufolio':'1'})
            with urlopen(req) as r:return json.load(r)
        try:
            for _ in range(100):
                try:urlopen(base+'/manifest.webmanifest',timeout=1).close();break
                except OSError:time.sleep(.1)
            for day,shares,price,note in [('2026-02-05',4,188,'每月定投 · 示例'),('2026-05-05',3,192,'持续积累 · 示例'),('2026-09-05',2,182,'长期计划 · 示例')]:
                api('/api/trades',{'symbol':'QQQM','trade_date':day,'shares':shares,'price':price,'note':note})
            api('/api/watchlist',{'symbol':'QQQM','threshold':.5},'PUT');api('/api/scan',{})
            with sync_playwright() as p:
                options={'headless':True}
                if os.name=='nt':options['channel']='msedge'
                browser=p.chromium.launch(**options)
                context=browser.new_context(http_credentials={'username':'admin','password':'preview-only'},viewport={'width':1440,'height':1040},device_scale_factor=1)
                page=context.new_page();page.emulate_media(reduced_motion='reduce');page.goto(base);expect(page.locator('#value')).to_have_text('$1,842.75')
                page.evaluate("const label=document.querySelector('header small');if(label)label.textContent='长期投资，从容积累 · 示例数据'")
                page.screenshot(path=str(OUT/'web-overview.png'))
                bars='\n'.join(f'2025-{i:02d}-03,{price}' for i,price in enumerate([100,104,97,90,95,103,109,106,115,120,118,130],1))
                page.locator("button[data-view='discover']").click()
                page.locator('#backtest [name=bars]').fill(bars);page.locator('#backtest button').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                page.locator('#backtest-panel').screenshot(path=str(OUT/'web-backtest.png'))
                page.locator("button[data-discover='monitoring']").click()
                page.locator('#monitoring').screenshot(path=str(OUT/'web-monitoring.png'))
                page.locator("button[data-view='assets']").click()
                page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)');page.wait_for_timeout(400)
                page.screenshot(path=str(OUT/'mobile-overview.png'))
                page.locator("button[data-view='records']").click();page.wait_for_timeout(200)
                page.screenshot(path=str(OUT/'mobile-trades.png'))
                page.locator("button[data-view='settings']").click()
                page.screenshot(path=str(OUT/'mobile-settings.png'))
                browser.close()
            print('Captured desktop and mobile previews using synthetic data in an isolated database.')
        finally:process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
