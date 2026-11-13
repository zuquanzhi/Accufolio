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
from datetime import date, timedelta
import math
import uvicorn
import apps.api.main as api
import apps.api.extended as monitoring
import apps.api.dashboard as dashboard
from apps.api.allocation import AllocationPlan, save_plan
from packages.providers import Quote, QuoteResult
class PreviewDate(date):
    @classmethod
    def today(cls): return cls(2026,11,13)
api.date=PreviewDate
dashboard.date=PreviewDate
prices={'QQQM':204.75,'VOO':520.40,'AAPL':225.60}
quote=lambda symbol: QuoteResult(Quote(symbol,prices.get(symbol,204.75),0.84,'demo',1794528000),False,())
api.get_quote=quote
dashboard.get_quote=quote
def history(symbol,start=None,end=None):
    first=date(2026,1,1);last=PreviewDate.today();length=(last-first).days
    bars=[]
    for index in range(length+1):
        day=first+timedelta(days=index);phase=index/length
        price=prices.get(symbol,204.75)*(.82+.18*phase+.035*math.sin(phase*6*math.pi))
        if (not start or day>=start) and (not end or day<=end):bars.append({'day':day.isoformat(),'close':round(price,2)})
    return {'bars':bars,'source':'demo','cached':True,'stale':False,'warnings':['示例数据 · 未复权'],'errors':[]}
dashboard.get_history=history
monitoring.get_quote=quote
monitoring.evaluate_calm=lambda symbol: {'status':'skipped','reason':'示例数据，不计算退潮信号'}
for symbol,day,shares,price,side in [('QQQM','2026-01-05',4,180,'buy'),('QQQM','2026-05-05',3,188,'buy'),('QQQM','2026-09-05',2,182,'buy'),('VOO','2026-02-05',3,460,'buy'),('VOO','2026-08-05',2,485,'buy'),('AAPL','2026-03-05',5,190,'buy'),('AAPL','2026-06-05',4,195,'buy'),('AAPL','2026-10-05',1,220,'sell')]:
    api.mutate_trade('add',api.Trade(symbol=symbol,trade_date=day,shares=shares,price=price,side=side,note='长期计划 · 示例'),None,'preview')
save_plan(AllocationPlan(reserve_amount=3750.45,reserve_as_of='2026-11-13',investment_targets={'QQQM':35,'VOO':40,'AAPL':25},total_targets={'QQQM':20,'VOO':30,'AAPL':20,'__RESERVE__':30},note='示例配置'),0,'preview-allocation','preview')
uvicorn.run(api.app,host='127.0.0.1',port=int(__import__('os').environ['PREVIEW_PORT']),log_level='warning')
'''

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'ACCUFOLIO_WEBHOOK_URL':'','ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'preview-only','ACCUFOLIO_DB':str(Path(temp)/'demo.db'),'PREVIEW_PORT':str(port)}
        process=subprocess.Popen([sys.executable,'-c',DEMO_SERVER],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        base=f'http://127.0.0.1:{port}'
        def api(path,data,method='POST'):
            req=Request(base+path,data=json.dumps(data).encode(),method=method,headers={'Authorization':'Basic '+base64.b64encode(b'admin:preview-only').decode(),'Content-Type':'application/json','X-Accufolio':'1'})
            with urlopen(req) as r:return json.load(r)
        try:
            for _ in range(100):
                try:urlopen(base+'/manifest.webmanifest',timeout=1).close();break
                except OSError:time.sleep(.1)
            api('/api/watchlist',{'symbol':'QQQM','threshold':.5},'PUT');api('/api/scan',{})
            with sync_playwright() as p:
                options={'headless':True}
                if os.name=='nt':options['channel']='msedge'
                browser=p.chromium.launch(**options)
                context=browser.new_context(http_credentials={'username':'admin','password':'preview-only'},viewport={'width':1440,'height':1040},device_scale_factor=1)
                page=context.new_page();page.emulate_media(reduced_motion='reduce');page.goto(base);expect(page.locator('#value')).to_have_text('$6,249.55')
                expect(page.locator('#portfolio-chart .chart-line')).to_have_count(2)
                page.evaluate("const label=document.querySelector('header small');if(label)label.textContent='长期投资，从容积累 · 示例数据'")
                page.screenshot(path=str(OUT/'web-overview.png'))
                page.set_viewport_size({'width':1440,'height':1500})
                bars=[{'day':f'2025-{i:02d}-03','close':price} for i,price in enumerate([100,104,97,90,95,103,109,106,115,120,118,130],1)]
                page.route('**/api/history?*',lambda route:route.fulfill(json={'symbol':'QQQM','bars':bars,'source':'demo','cached':False,'stale':False,'adjustment':'unadjusted','adjustment_note':'示例数据 · 未复权','errors':[]}))
                page.locator("a[data-view='discover']").click()
                page.locator('#backtest [name=start]').fill('2025-01-01')
                page.locator('#backtest [name=end]').fill('2025-12-31')
                page.locator('#run-history').click()
                expect(page.locator('#backtest-result')).to_contain_text('固定月投')
                page.set_viewport_size({'width':1440,'height':2100})
                expect(page.locator('#backtest-chart .chart-line')).to_have_count(5)
                page.locator('#backtest-result .backtest-detail > summary').click()
                page.locator('#backtest-panel').screenshot(path=str(OUT/'web-backtest.png'))
                page.locator('#backtest-result .backtest-detail > summary').click()
                page.locator("button[data-discover='monitoring']").click()
                expect(page.locator('#watch-items')).to_contain_text('QQQM')
                expect(page.locator('#notification-status')).not_to_contain_text('正在读取')
                page.locator('#monitoring').screenshot(path=str(OUT/'web-monitoring.png'))
                page.locator("a[data-view='assets']").click()
                page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)');page.wait_for_timeout(400)
                page.screenshot(path=str(OUT/'mobile-overview.png'))
                page.locator('[data-scope=total]').click()
                page.locator('.allocation-panel').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'mobile-allocation.png'))
                page.locator('[data-scope=investments]').click()
                page.evaluate('scrollTo(0,0)')
                page.locator("a[data-view='records']").click();page.wait_for_timeout(200)
                page.screenshot(path=str(OUT/'mobile-trades.png'))
                page.locator("a[data-view='discover']").click()
                page.locator("button[data-discover='backtest']").click()
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(OUT/'mobile-discover.png'))
                page.locator("a[data-view='settings']").click()
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(OUT/'mobile-settings.png'))
                browser.close()
            print('Captured desktop and mobile previews using synthetic data in an isolated database.')
        finally:process.terminate();process.wait(timeout=15)

if __name__=='__main__':main()
