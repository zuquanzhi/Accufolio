"""Five-page browser checks for account assets, investments and display currencies.

A disposable authenticated app serves the real UI. All financial/provider data
is routed to deterministic fixtures; the UI never submits financial mutations.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dashboard_smoke import TRADES,payload

RATES={'base':'USD','rates':{'USD':1,'CNY':7.1,'HKD':7.8,'EUR':.9},'source':'ECB','as_of':'2026-11-13','available':True,'stale':False}
TARGETS={'securities':{'AAA':50,'BBB':40,'CCC':10},'net_worth':{'account:1':50,'account:2':50},'revision':1,'updated_at':'2026-11-13T00:00:00Z'}
ASSETS={'as_of':'2026-11-13','base':'USD','net_assets_usd':1000,'securities_value_usd':400,'cash_value_usd':600,'complete':True,'missing_fx':[],
 'accounts':[{'id':1,'name':'长线证券','kind':'broker','purpose':'investing','currency':'USD','balance':0,'as_of':'2026-11-13','cash_value_usd':0,'securities_value_usd':400,'value_usd':400,'revision':1},
             {'id':2,'name':'工资卡','kind':'bank','purpose':'savings','currency':'CNY','balance':4260,'as_of':'2026-11-13','cash_value_usd':600,'securities_value_usd':0,'value_usd':600,'revision':1}],
 'fx':RATES,'warnings':[]}
BACKTEST={'strategies':{key:{'invested':200,'value':240,'return_rate':.2,'xirr':.1,'equity':[['2024-01-02',100],['2024-02-02',240]]}for key in ['fixed','quarterly','dip','blend']},'market':{'twr':.2,'max_drawdown':.1,'drawdown_date':'2024-02-02'}}

def main():
 with tempfile.TemporaryDirectory() as temp:
  sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
  env={**os.environ,'ACCUFOLIO_USER':'admin','ACCUFOLIO_PASSWORD':'preferences-test','ACCUFOLIO_DB':str(Path(temp)/'preferences.db')}
  process=subprocess.Popen([sys.executable,'-m','uvicorn','apps.api.main:app','--port',str(port),'--timeout-graceful-shutdown','3'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  base=f'http://127.0.0.1:{port}'
  try:
   for _ in range(100):
    try:urlopen(base+'/manifest.webmanifest',timeout=1).close();break
    except OSError:time.sleep(.1)
   else:raise RuntimeError('Test service did not start')
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,**({'channel':'msedge'} if os.name=='nt' else {}))
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    login=context.request.post(base+'/api/session',headers={'X-Accufolio':'1'},data={'username':'admin','password':'preferences-test','remember':True});assert login.ok,login.text()
    page=context.new_page();errors=[];requests=[];state={'fx_status':200,'stale':False,'quote_lag':False,'incomplete':False}
    page.on('pageerror',lambda error:errors.append(str(error)));page.on('request',lambda req:requests.append((req.method,req.url,req.post_data or '')))
    def dashboard(route):
     args=parse_qs(urlsplit(route.request.url).query);result=payload(args.get('period',['1y'])[0],args.get('include_history',['true'])[0]!='false')
     if state['quote_lag']:
      result['market']={'last_completed_session':'2026-11-12'}
      for row in result['holdings']:row['quote_timestamp']=1794258000
     route.fulfill(json=result)
    def assets(route):
     result=deepcopy(ASSETS)
     if state['incomplete']:result.update(complete=False,net_assets_usd=None,missing_fx=['CNY'],cash_value_usd=None);result['accounts'][1]['value_usd']=None
     route.fulfill(json=result)
    page.route('**/api/dashboard*',dashboard);page.route('**/api/assets',assets)
    page.route('**/api/allocation',lambda r:r.fulfill(json=TARGETS));page.route('**/api/trades',lambda r:r.fulfill(json=TRADES))
    page.route('**/api/accounts/*/cash-entries*',lambda r:r.fulfill(json=[{'id':1,'account_id':2,'entry_date':'2026-11-13','effective_date':'2026-11-13','amount':4260,'currency':'CNY','kind':'deposit','note':'工资'}]))
    page.route('**/api/settings',lambda r:r.fulfill(json={'display_currency':'USD','market_timezone':'America/New_York','revision':1}))
    page.route('**/api/version',lambda r:r.fulfill(json={'version':'preferences-fixture','release':'2.0.0'}))
    page.route('**/api/fx',lambda r:r.fulfill(status=state['fx_status'],json={**RATES,'stale':state['stale']}))
    def fx_history(route):
     args=parse_qs(urlsplit(route.request.url).query)
     if args['start'][0].startswith('2024'):
      points=[{'day':'2024-01-02','rates':{'USD':1,'CNY':6}},{'day':'2024-02-02','rates':{'USD':1,'CNY':7}}]
     else:
      points=[{'day':p['day'],'rates':{'USD':1,'CNY':None if i==4 else 6 if i==0 else 7,'HKD':7.8,'EUR':.9}}for i,p in enumerate(payload()['series'])]
     route.fulfill(json={'base':'USD','points':points,'stale':False})
    page.route('**/api/fx/history?*',fx_history)
    health_reply={'release':'2.0.0','database':'ok','worker':{'status':'stale','heartbeat_at':'2026-11-13T12:00:00+00:00'},'backup':{'status':'ok','last_success_at':'2026-11-13T01:00:00+00:00','keep':30}}
    page.route('**/api/health',lambda r:r.fulfill(json=health_reply))
    page.route('**/api/backtest',lambda r:r.fulfill(json=BACKTEST))
    tab=lambda name:page.locator('a[data-view="'+name+'"]').click()
    def discovery(tool):
     if page.locator('#discover-back').is_visible():
      page.locator('#discover-back').click();expect(page.locator('#discover-home')).to_be_visible()
     page.locator('[data-discover-tool="'+tool+'"]').click();expect(page.locator('[data-discover-panel="'+tool+'"]').first).to_be_visible()
    page.goto(base);expect(page.locator('#assets-total')).to_have_text('$1,000.00');assert page.url==base+'/assets'
    expect(page.locator('.tab-bar a')).to_have_count(5);expect(page.locator('#asset-accounts')).to_contain_text('工资卡');expect(page.locator('#asset-accounts')).to_contain_text('储蓄 · CNY')
    expect(page.locator('#asset-accounts')).to_contain_text('CN¥4,260.00');expect(page.locator('#assets-securities')).to_have_text('$400.00');expect(page.locator('#assets-cash')).to_have_text('$600.00')
    expect(page.locator('#total-allocation-legend [data-account-id="1"]')).to_contain_text('40.0%');expect(page.locator('#total-allocation-legend [data-account-id="1"]')).to_contain_text('+$100.00')
    if os.environ.get('PREFERENCES_CAPTURE_DIR'):
     out=Path(os.environ['PREFERENCES_CAPTURE_DIR']);out.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/'assets.png'),full_page=True)
    page.evaluate('window.preferenceDocumentMarker=17')
    page.locator('#asset-accounts [data-account-id="2"]').click();expect(page.locator('#account-native-balance')).to_have_text('CN¥4,260.00');expect(page.locator('#account-cash-entries')).to_contain_text('工资');assert page.url.endswith('/assets?account=2')
    page.go_back();expect(page.locator('#assets-total')).to_be_visible()
    tab('investments');expect(page.locator('#value')).to_have_text('$400.00');expect(page.locator('#pnl')).to_have_text('$72.00');expect(page.locator('#summary-realized')).to_have_text('$10.00')
    if os.environ.get('PREFERENCES_CAPTURE_DIR'):page.screenshot(path=str(out/'investments.png'),full_page=True)
    expect(page.locator('#portfolio-chart .chart-line')).to_have_count(2);expect(page.locator('#asset-scope')).to_have_count(0)
    expect(page.locator('#holdings-list')).to_be_hidden();before=len([r for r in requests if '/api/dashboard?' in r[1]])
    page.locator('[data-asset-detail=allocation]').click();expect(page.locator('#allocation-ring')).to_be_visible();assert page.url.endswith('/investments?detail=allocation')
    page.go_back();expect(page.locator('#value')).to_be_visible();page.locator('[data-asset-detail=performance]').click();expect(page.locator('#buy-total')).to_be_visible()
    expect(page.locator('#dashboard-facts')).to_contain_text('XIRR（美元口径）');page.locator('#asset-detail-back').click();expect(page.locator('#value')).to_be_visible()
    assert len([r for r in requests if '/api/dashboard?' in r[1]])==before
    entries=page.locator('.overview-links').bounding_box();nav=page.locator('.tab-bar').bounding_box();assert entries['y']+entries['height']<nav['y'],(entries,nav)
    tab('records');expect(page.locator('#trades')).to_contain_text('$110.00')
    tab('discover');discovery('backtest');page.locator('#manual-data summary').click();page.locator('#backtest [name=bars]').fill('2024-01-02,100\n2024-02-02,120');page.locator('#run-manual').click();expect(page.locator('#backtest-chart')).to_be_visible()
    tab('settings');expect(page.locator('#default-scope')).to_have_count(0);expect(page.locator('#migration')).to_have_count(0);expect(page.locator('#ledger-mode')).to_have_count(0)
    page.select_option('#display-currency','CNY');expect(page.locator('#currency-status')).to_contain_text('7.1')
    page.select_option('#display-timezone','Asia/Shanghai');expect(page.locator('#timezone-status')).to_contain_text('北京')
    assert '11/14 05:00' in page.evaluate("formatTimestamp('2026-11-13 21:00:00')")
    tab('assets');expect(page.locator('#assets-total')).to_have_text('CN¥7,100.00');expect(page.locator('#asset-accounts')).to_contain_text('CN¥4,260.00')
    tab('investments');expect(page.locator('#value')).to_have_text('CN¥2,840.00');expect(page.locator('#valuation-label')).to_contain_text('最近行情')
    expect(page.locator('#portfolio-chart .chart-values')).to_contain_text('CN¥2,800.00');expect(page.locator('#history-coverage')).to_contain_text('缺少历史汇率')
    page.locator('[data-asset-detail=allocation]').click();expect(page.locator('[data-symbol=AAA].allocation-row')).to_contain_text('-CN¥284.00')
    page.locator('#asset-detail-back').click();page.locator('[data-asset-detail=performance]').click();expect(page.locator('#buy-total')).to_have_text('CN¥3,038.80');expect(page.locator('#realized')).to_have_text('CN¥71.00')
    tab('records');expect(page.locator('#trades')).to_contain_text('CN¥781.00')
    tab('discover');discovery('backtest');expect(page.locator('#backtest-chart .chart-values')).to_contain_text('CN¥1,680.00');page.locator('.backtest-detail summary').click();expect(page.locator('.backtest-detail')).to_contain_text('CN¥1,400.00')
    tab('settings');page.locator('#system summary').click();expect(page.locator('#health')).to_contain_text('已停滞');expect(page.locator('#health')).to_contain_text('20:00')
    expect(page.locator('#backup-status')).to_contain_text('09:00');expect(page.locator('#backup-status')).not_to_contain_text('20:00');expect(page.locator('#backup-status')).to_contain_text('30 份')
    assert page.evaluate('window.preferenceDocumentMarker')==17
    tab('settings');page.reload();expect(page.locator('#display-currency')).to_have_value('CNY');expect(page.locator('#display-timezone')).to_have_value('Asia/Shanghai')
    health_reply['backup']['status']='failed';page.reload();expect(page.locator('#backup-status')).to_contain_text('自动备份失败');expect(page.locator('#backup-status')).to_contain_text('09:00')
    health_reply['backup'].update(status='not_created',last_success_at=None);page.reload();expect(page.locator('#backup-status')).to_contain_text('尚无成功的自动备份');expect(page.locator('#backup-status')).not_to_contain_text('09:00')
    tab('investments');expect(page.locator('#value')).to_have_text('CN¥2,840.00')
    state['fx_status']=503;page.reload();expect(page.locator('#value')).to_have_text('$400.00');expect(page.locator('#quote-as-of')).to_contain_text('汇率暂不可用');expect(page.locator('#valuation-label')).to_contain_text('USD')
    state.update(fx_status=200,stale=True,quote_lag=True);page.reload();expect(page.locator('#value')).to_have_text('CN¥2,840.00');expect(page.locator('#quote-as-of')).to_contain_text('过期汇率');expect(page.locator('#quote-as-of')).to_contain_text('行情滞后');expect(page.locator('#quote-as-of')).to_contain_text('北京')
    state['incomplete']=True;tab('assets');expect(page.locator('#assets-total')).to_have_text('—');expect(page.locator('#assets-total-note')).to_contain_text('缺少 CNY 汇率');expect(page.locator('#asset-accounts')).to_contain_text('CN¥4,260.00')
    for width in [320,390,1280]:
     page.set_viewport_size({'width':width,'height':844})
     for name in ['assets','investments','records','discover','settings']:
      tab(name);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,name)
      if name=='investments':
       for detail in ['allocation','performance']:
        page.locator('[data-asset-detail='+detail+']').click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,detail)
        page.locator('#asset-detail-back').click();expect(page.locator('#value')).to_be_visible()
    page.goto(base+'/investments?detail=allocation');expect(page.locator('#holdings-list')).to_be_visible();page.locator('#asset-detail-back').click();expect(page.locator('#value')).to_be_visible()
    page.goto(base+'/assets?account=2');expect(page.locator('#account-cash-entries')).to_contain_text('工资');page.locator('#account-back').click();expect(page.locator('#assets-total')).to_be_visible()
    assert all(method in ('GET','HEAD') or '/api/backtest' in url for method,url,body in requests),requests
    assert not errors,errors;browser.close()
   print('Preferences checks passed: five distinct pages, named-account net assets/cashflows, current vs historical FX, USD returns, read-only views, persistent currency, navigation and mobile layout.')
  finally:
   process.terminate()
   try:process.wait(timeout=8)
   except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

if __name__=='__main__':main()
