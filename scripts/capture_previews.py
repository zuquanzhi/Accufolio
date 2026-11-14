"""Capture real v2 pages using isolated, explicitly synthetic financial data.

The fixtures patch quote, daily-price and FX providers inside this disposable
process; no production database or external provider is read or changed.
"""
import os,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets'/'previews'
DEMO_SERVER=r'''
from datetime import date, timedelta, datetime
import math
import uvicorn
import apps.api.main as api
import apps.api.dashboard as dashboard
import apps.api.history as history_api
import apps.api.fx as fx_api
import packages.providers as providers
import packages.core.market_time as market_time
from apps.api.accounts import Account, CashEntry, save_account, add_cash_entry
from apps.api.allocation import save_targets
from packages.providers import Quote, QuoteResult

TODAY=date(2026,11,13)
instant=datetime.fromisoformat('2026-11-13T17:00:00-05:00')
clock=market_time.market_clock(instant)
api.et_today=lambda:TODAY
market_time.et_today=lambda:TODAY
dashboard.market_clock=lambda:clock
prices={'QQQM':204.75,'VOO':520.40,'AAPL':225.60}
quote=lambda symbol: QuoteResult(Quote(symbol,prices.get(symbol,204.75),.84,'demo',instant.timestamp()),False,())
api.get_quote=dashboard.get_quote=providers.get_quote=quote

def daily(symbol,start=None,end=None):
    first=date(2026,1,1);length=(TODAY-first).days;bars=[]
    for index in range(length+1):
        day=first+timedelta(days=index);phase=index/length
        price=prices.get(symbol,204.75)*(.82+.18*phase+.035*math.sin(phase*6*math.pi))
        if (not start or day>=start) and (not end or day<=end):bars.append({'day':day.isoformat(),'close':round(price,2)})
    return {'symbol':symbol,'bars':bars,'source':'demo','cached':True,'stale':False,'adjustment':'unadjusted',
            'adjustment_note':'示例数据 · 未复权','warnings':['示例数据 · 未复权'],'errors':[]}
dashboard.get_history=history_api.get_history=daily
fx_current={'base':'USD','rates':{'USD':1,'CNY':7.1,'HKD':7.8,'EUR':.93},'as_of':TODAY.isoformat(),
            'fetched_at':instant.isoformat(),'source':'DEMO','cached':True,'stale':False,'available':True,'errors':[]}
fx_api.get_fx=lambda:fx_current

def historic_fx(days,base='USD'):
    points=[]
    for day in days:
        day=date.fromisoformat(day) if isinstance(day,str) else day
        value=7.0+.1*math.sin((day-date(2026,1,1)).days/50)
        points.append({'day':day.isoformat(),'rates':{'USD':1,'CNY':value,'HKD':7.8,'EUR':.93},
                       'rate_dates':{c:day.isoformat() for c in ['USD','CNY','HKD','EUR']},'source':'DEMO'})
    return {'base':base,'points':points,'cached':True,'stale':False,'errors':[],'source':'DEMO'}
fx_api.get_historical_fx=historic_fx

# Every amount below is invented for documentation, never a user's financial data.
with api.db() as con:pass
broker=save_account(Account(name='长线投资',kind='broker',purpose='investing',currency='USD',institution='示例券商'),1,0,'preview-broker',False,'preview')
savings=save_account(Account(name='工资结余',kind='bank',purpose='savings',currency='CNY',institution='示例银行'),None,0,'preview-savings',False,'preview')
emergency=save_account(Account(name='安心储备',kind='bank',purpose='emergency',currency='USD',institution='示例银行'),None,0,'preview-emergency',False,'preview')

def cash(account,day,kind,amount,key,note):
    with api.db() as con:revision=con.execute('SELECT revision FROM accounts WHERE id=?',(account['id'],)).fetchone()[0]
    return add_cash_entry(CashEntry(account_id=account['id'],entry_date=day,kind=kind,amount=amount,currency=account['currency'],note=note),revision,key,False,'preview')
cash(broker,'2026-01-02','balance_open',6266.45,'preview-broker-opening','投资资金 · 示例')
cash(savings,'2026-01-02','balance_open',14000,'preview-savings-opening','年初结余 · 示例')
cash(savings,'2026-11-10','deposit',600,'preview-salary','本月工资结余 · 示例')
cash(savings,'2026-11-12','withdraw',-400,'preview-expense','日常支出 · 示例')
cash(emergency,'2026-01-02','balance_open',1000,'preview-emergency-opening','应急储备 · 示例')
for symbol,day,shares,price,side in [('QQQM','2026-01-05',4,180,'buy'),('QQQM','2026-05-05',3,188,'buy'),('QQQM','2026-09-05',2,182,'buy'),('VOO','2026-02-05',3,460,'buy'),('VOO','2026-08-05',2,485,'buy'),('AAPL','2026-03-05',5,190,'buy'),('AAPL','2026-06-05',4,195,'buy'),('AAPL','2026-10-05',1,220,'sell')]:
    api.mutate_trade('add',api.Trade(account_id=broker['id'],symbol=symbol,trade_date=day,shares=shares,price=price,side=side,fee=1,strategy='dca',note='长期计划 · 示例'),None,'preview')
    cash(broker,day,'withdraw' if side=='buy' else 'deposit',-(shares*price+1) if side=='buy' else shares*price-1,'preview-settlement-'+symbol+'-'+day,symbol+' 交易结算 · 示例')
save_targets('securities',{'QQQM':35,'VOO':40,'AAPL':25},0,'preview-security-targets',False,'preview')
save_targets('net_worth',{f"account:{broker['id']}":65,f"account:{savings['id']}":20,f"account:{emergency['id']}":15},1,'preview-account-targets',False,'preview')
uvicorn.run(api.app,host='127.0.0.1',port=int(__import__('os').environ['PREVIEW_PORT']),log_level='warning')
'''


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'preview-only',
             'ACCUFOLIO_DB':str(Path(temp)/'demo.db'),'PREVIEW_PORT':str(port)}
        log_path=Path(temp)/'preview.log'
        with log_path.open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-c',DEMO_SERVER],cwd=ROOT,env=env,stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(150):
                try:urlopen(base+'/manifest.webmanifest',timeout=1).close();break
                except OSError:
                    if process.poll() is not None:raise RuntimeError(log_path.read_text(encoding='utf-8'))
                    time.sleep(.1)
            else:raise RuntimeError('Preview server did not start: '+log_path.read_text(encoding='utf-8'))
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,**({'channel':'msedge'} if os.name=='nt' else {}))
                context=browser.new_context(viewport={'width':1440,'height':1160},device_scale_factor=1,reduced_motion='reduce')
                assert context.request.post(base+'/api/session',data={'username':'admin','password':'preview-only','remember':True},headers={'X-Accufolio':'1'}).ok
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                tab=lambda name:page.locator(f'a[data-view="{name}"]').click()
                def capture(name):
                    page.evaluate('scrollTo(0,0)');page.screenshot(path=str(OUT/name))
                page.goto(base);expect(page.locator('#assets-total')).to_have_text('$10,000.00')
                expect(page.locator('#asset-accounts .named-account')).to_have_count(3)
                capture('web-overview.png')
                page.set_viewport_size({'width':1440,'height':900});capture('poster-web-overview.png');page.set_viewport_size({'width':1440,'height':1160})
                tab('investments');expect(page.locator('#value')).to_have_text('$6,249.55');expect(page.locator('#portfolio-chart .chart-line')).to_have_count(2)
                capture('web-investments.png')
                page.locator('[data-asset-detail=allocation]').click();capture('web-allocation.png')
                page.locator('#asset-detail-back').click();expect(page.locator('#value')).to_be_visible()
                tab('discover');expect(page.locator('#discover-home')).to_be_visible();capture('web-discover.png')
                page.locator('[data-discover-tool=backtest]').click()
                bars=[{'day':f'2025-{i:02d}-03','close':price} for i,price in enumerate([100,104,97,90,95,103,109,106,115,120,118,130],1)]
                page.route('**/api/history?*',lambda route:route.fulfill(json={'symbol':'QQQM','bars':bars,'source':'demo','cached':False,'stale':False,'adjustment':'unadjusted','adjustment_note':'示例数据 · 未复权','errors':[]}))
                page.locator('#backtest [name=start]').fill('2025-01-01');page.locator('#backtest [name=end]').fill('2025-12-31');page.locator('#run-history').click()
                expect(page.locator('#backtest-chart .chart-line')).to_have_count(5)
                page.set_viewport_size({'width':1440,'height':2100});page.locator('.backtest-detail > summary').click();page.locator('#backtest-panel').screenshot(path=str(OUT/'web-backtest.png'));page.locator('.backtest-detail > summary').click()
                page.set_viewport_size({'width':390,'height':844})
                tab('assets');capture('mobile-overview.png')
                page.locator('#asset-accounts [data-account-id="2"]').click();expect(page.locator('#account-cash-entries')).to_contain_text('本月工资结余');capture('mobile-account.png')
                page.locator('#account-back').click();expect(page.locator('#assets-total')).to_be_visible()
                tab('investments');capture('mobile-investments.png')
                page.locator('[data-asset-detail=allocation]').click();capture('mobile-allocation.png')
                page.locator('#asset-detail-back').click();expect(page.locator('#value')).to_be_visible()
                tab('records');expect(page.locator('#trades')).to_contain_text('QQQM');capture('mobile-trades.png')
                tab('discover');expect(page.locator('#discover-home')).to_be_visible();capture('mobile-discover.png')
                page.locator('[data-discover-tool=backtest]').click();capture('mobile-backtest.png')
                page.locator('#discover-back').click();expect(page.locator('#discover-home')).to_be_visible();page.locator('[data-discover-tool=income]').click()
                page.locator('#income-form [name=gross]').fill('10000');page.locator('#income-form [name=fund]').fill('800');page.locator('#income-form [name=social]').fill('1000')
                page.locator('#income-form button[type=submit]').click();expect(page.locator('#income-result')).to_be_visible();expect(page.locator('#income-net')).to_contain_text('8,104')
                capture('mobile-income-form.png')
                page.locator('#income-result').evaluate("element=>element.scrollIntoView({block:'start',behavior:'instant'})")
                page.screenshot(path=str(OUT/'mobile-income.png'))
                tab('settings');capture('mobile-settings.png')
                page.select_option('#display-currency','CNY');tab('assets');expect(page.locator('#assets-total')).to_have_text('CN¥71,000.00');capture('mobile-currency.png')
                tab('investments');expect(page.locator('#portfolio-chart .chart-line')).to_have_count(2);expect(page.locator('#history-coverage')).to_contain_text('按各交易日历史汇率折算');capture('mobile-investments-cny.png')
                assert not errors,errors
                browser.close()
            print('Captured v2 Assets, Investments, account cashflows and five mobile pages with synthetic data only.')
        finally:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

if __name__=='__main__':main()
