"""Exercise the real app over local HTTPS using an opt-in, test-only debug CA.

prepare: make an ephemeral CA/certificate before -PandroidIntegration=true builds.
run APK: serve the actual FastAPI app and drive the original MainActivity by touch.
No fixtures, CA, alternate WebViewClient, or relaxed TLS policy enter the release APK.
"""
import os
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work' / 'android-integration'
CERT = WORK / 'cert.pem'
KEY = WORK / 'key.pem'
NAV = WORK / 'navigation.log'
PORT = 8443


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    config=WORK/'openssl.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=v3\n[dn]\n[v3]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyCertSign,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:10.0.2.2,IP:127.0.0.1\n',encoding='utf-8')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
                    '-days', '2', '-subj', '/CN=Accufolio disposable Android integration',
                    '-config', str(config),
                    '-keyout', str(KEY), '-out', str(CERT)], check=True, capture_output=True)
    raw = ROOT / 'apps/mobile/app/build/generated/integration/res/raw'
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CERT, raw / 'integration_ca.crt')
    print('Prepared ephemeral debug-only HTTPS trust anchor.')


def serve():
    from datetime import date, timedelta
    import uvicorn
    import apps.api.main as api
    import apps.api.dashboard as dashboard
    from apps.api.allocation import save_targets
    from apps.api.accounts import Account, CashEntry, save_account, add_cash_entry
    import apps.api.fx as fx
    import packages.providers as providers
    from packages.providers import Quote, QuoteResult
    quote = lambda symbol: QuoteResult(Quote(symbol, 225.6 if symbol == 'AAPL' else 204.75, .84, 'test', time.time()), False, ())
    api.get_quote = quote
    dashboard.get_quote = quote
    providers.get_quote = quote
    fx.get_fx=lambda:{'base':'USD','rates':{'USD':1,'CNY':7.2,'HKD':7.8,'EUR':.8},'as_of':date.today().isoformat(),'source':'fixture','available':True,'cached':True,'stale':False,'errors':[]}
    fx.get_historical_fx=lambda days,base='USD',**kw:{'base':base,'points':[{'day':str(day),'rates':{'USD':1,'CNY':7.2,'HKD':7.8,'EUR':.8},'rate_dates':{c:str(day) for c in ['USD','CNY','HKD','EUR']},'source':'fixture'} for day in days],'cached':True,'stale':False,'errors':[]}
    def history(symbol, start, end):
        count=(end-start).days
        return {'bars':[{'day':(start+timedelta(days=i)).isoformat(),
                         'close':round(quote(symbol).quote.price*(.85+.15*i/max(1,count)),2)} for i in range(count+1)],
                'source':'fixture','cached':True,'stale':False,'errors':[],'warnings':[]}
    dashboard.get_history = history
    api.mutate_trade('add', api.Trade(symbol='QQQM', trade_date=date(2025, 1, 2), shares=2, price=180), None, 'integration')
    api.mutate_trade('add', api.Trade(symbol='AAPL', trade_date=date(2025, 2, 3), shares=3, price=190), None, 'integration')
    account=save_account(Account(name='Savings',kind='bank',purpose='savings',currency='USD'),None,0,'integration-account',dry_run=False,actor='integration')
    add_cash_entry(CashEntry(account_id=account['id'],entry_date=date.today(),kind='balance_open',amount=913.7,currency='USD'),0,'integration-opening',dry_run=False,actor='integration')
    save_targets('securities',{'QQQM':40,'AAPL':60},0,'integration-securities',dry_run=False,actor='integration')
    save_targets('net_worth',{'account:1':60,'account:'+str(account['id']):40},1,'integration-accounts',dry_run=False,actor='integration')

    @api.app.middleware('http')
    async def log_navigation(request, call_next):
        response = await call_next(request)
        if request.url.path in ('/assets', '/investments', '/records', '/discover', '/settings') and response.status_code == 200:
            with NAV.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'path':request.url.path,'destination':request.headers.get('sec-fetch-dest','')}) + '\n')
        return response

    uvicorn.run(api.app, host='0.0.0.0', port=PORT, ssl_keyfile=str(KEY), ssl_certfile=str(CERT),
                log_level='warning', timeout_graceful_shutdown=5)


def adb(*args):
    return subprocess.check_output(['adb', *args], text=True)


def screen():
    # A failed dump can leave the previous XML on the device. Never treat that as current UI.
    last_error = None
    for _ in range(3):
        try:
            adb('shell', 'rm', '-f', '/sdcard/accufolio-ui.xml')
            result = adb('shell', 'uiautomator', 'dump', '/sdcard/accufolio-ui.xml')
            if 'dumped' not in result.lower():
                raise RuntimeError('No fresh UI hierarchy: '+result.strip())
            tree = ET.fromstring(adb('shell', 'cat', '/sdcard/accufolio-ui.xml'))
            if not list(tree.iter('node')):
                raise RuntimeError('Empty UI hierarchy')
            return tree
        except (subprocess.CalledProcessError, ET.ParseError, RuntimeError) as error:
            last_error=error;time.sleep(.4)
    raise RuntimeError('UI hierarchy unavailable') from last_error


def text(node):
    return node.attrib.get('text') or node.attrib.get('content-desc', '')


def items(ui):
    return [text(node) for node in ui.iter('node')]


def named(ui, value):
    matches = [node for node in ui.iter('node') if text(node) == value]
    if not matches:
        raise AssertionError((value, items(ui)))
    return matches[-1]


def bounds(node):
    return list(map(int, re.findall(r'\d+', node.attrib['bounds'])))


def tap(node):
    x1, y1, x2, y2 = bounds(node)
    assert x2 > x1 and y2 > y1, node.attrib
    adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))


def capture(filename):
    with (ROOT / 'work' / filename).open('wb') as stream:
        subprocess.run(['adb', 'exec-out', 'screencap', '-p'], stdout=stream, check=True)


def document_requests():
    entries = [json.loads(line) for line in NAV.read_text(encoding='utf-8').splitlines()] if NAV.exists() else []
    return [entry['path'] for entry in entries if entry['destination']=='document']


def wait_ui(predicate, description, reject_login=False, timeout=50):
    deadline=time.monotonic()+timeout
    last=[]
    while time.monotonic()<deadline:
        try:
            ui=screen();last=items(ui)
            if reject_login and any(item.startswith('登录 ') or item == '欢迎回来' for item in last):
                raise AssertionError(('Unexpected repeat authentication',last))
            if predicate(ui):return ui
        except RuntimeError:
            pass
        time.sleep(.3)
    raise AssertionError(('UI not ready',description,last))


def wait_page(route):
    distinctive={'/assets':'净资产','/investments':'持仓市值','/records':'搜索记录','/discover':'策略回测','/settings':'展示币种'}[route]
    def ready(ui):
        values=items(ui)
        return all(label in values for label in ['资产','投资','记录','发现','设置']) and any(distinctive in item for item in values)
    ui=wait_ui(ready,route,reject_login=True)
    for unexpected in ['应用设置','正在连接…','修改连接','重试']:
        assert unexpected not in items(ui),('Unexpected native footer',unexpected,items(ui))
    return ui


def wait_native(value):
    return wait_ui(lambda ui:value in items(ui),value,reject_login=True,timeout=20)


def scroll_to(value):
    width, height = list(map(int, re.findall(r'\d+', adb('shell', 'wm', 'size'))))[-2:]
    for _ in range(8):
        ui = screen()
        matches = []
        for node in ui.iter('node'):
            label = re.sub(r'\s+', ' ', text(node)).strip()
            clickable = node.attrib.get('clickable') == 'true'
            # WebView can expose a button's title and explanatory text as one
            # accessible label. Only accept that prefix on an enabled action,
            # not a surrounding section containing unrelated descendant text.
            if node.attrib.get('enabled') == 'false':
                continue
            if label == value:
                rank = 0 if clickable else 2
            elif clickable and label.startswith(value + ' '):
                rank = 1
            else:
                continue
            x1, y1, x2, y2 = bounds(node)
            if x2 > x1 and y2 > y1 and y1 > 30 and y2 < height-95:
                matches.append((rank, node))
        if matches:
            rank = min(item[0] for item in matches)
            best = [node for candidate_rank, node in matches if candidate_rank == rank]
            if len(best) != 1:
                raise AssertionError(('Ambiguous visible action', value, [node.attrib for node in best]))
            return best[0]
        adb('shell', 'input', 'swipe', str(width//2), str(int(height*.72)), str(width//2), str(int(height*.35)), '350')
        time.sleep(.3)
    raise AssertionError(('Unable to reach', value, items(screen())))


def run(apk):
    if not CERT.exists():
        raise RuntimeError('Run prepare before building the integration debug APK')
    NAV.write_text('', encoding='utf-8')
    with tempfile.TemporaryDirectory() as temp, (WORK/'server.log').open('w', encoding='utf-8') as log:
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'ACCUFOLIO_DB': str(Path(temp)/'integration.db'),
               'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'android-integration-test'}
        server = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve'], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            context = ssl.create_default_context(cafile=str(CERT))
            for _ in range(50):
                try:
                    urlopen(f'https://127.0.0.1:{PORT}/manifest.webmanifest', context=context, timeout=1).close()
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError('HTTPS integration server did not start')
            # The signed release was already smoke-tested; replace it only inside this disposable emulator.
            subprocess.run(['adb', 'uninstall', 'io.accufolio.app'], check=True, capture_output=True)
            subprocess.run(['adb', 'install', '-r', apk], check=True)
            adb('shell', 'am', 'start', '-W', '-n', 'io.accufolio.app/.MainActivity')
            time.sleep(2)
            ui = screen()
            entry = next(n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText')
            tap(entry)
            adb('shell', 'input', 'text', f'https://10.0.2.2:{PORT}')
            adb('shell', 'input', 'keyevent', '4')
            tap(named(screen(), '连接'))
            ui=wait_ui(lambda tree:'登录' in items(tree),'initial authentication')
            password = [n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText'][-1]
            tap(password);adb('shell', 'input', 'text', 'android-integration-test')
            adb('shell','input','keyevent','4')
            tap(named(screen(), '登录'))
            wait_ui(lambda tree:'欢迎回来' not in items(tree) and not any(item.startswith('登录 ') for item in items(tree)),'login dismissal')
            ui = wait_page('/assets')
            initial_documents=document_requests()
            assert initial_documents==['/assets'],initial_documents
            ui=wait_ui(lambda tree:any('$2,000.00' in item for item in items(tree)), 'net assets valuation',reject_login=True)
            capture('android-assets.png')
            assert any('净资产' in item for item in items(ui)),items(ui)
            tap(named(ui,'投资'));ui=wait_page('/investments')
            ui=wait_ui(lambda tree:any('$1,086.30' in item for item in items(tree)), 'securities-only valuation',reject_login=True)
            assert document_requests()==initial_documents,('Investments reloaded document',document_requests())
            capture('android-investments.png')
            for label, route in [('记录','/records'),('发现','/discover'),('设置','/settings')]:
                tap(named(ui,label));ui=wait_page(route)
                assert document_requests()==initial_documents,('Tab reloaded the document',route,document_requests())
                if route=='/discover':
                    tap(scroll_to('策略回测'))
                    wait_ui(lambda tree:any('获取行情并回测' in value for value in items(tree)),'discovery backtest detail',reject_login=True)
                    assert document_requests()==initial_documents,('Discovery detail reloaded document',document_requests())
                    adb('shell','input','keyevent','4')
                    ui=wait_ui(lambda tree:any('税后收入' in value for value in items(tree)) and any('策略回测' in value for value in items(tree)) and not any('获取行情并回测' in value for value in items(tree)), 'native Back to discovery entries',reject_login=True)
                    assert document_requests()==initial_documents,('Discovery back reloaded document',document_requests())
            capture('android-settings-page.png')
            # These taps are real input events, exercising MainActivity's origin/frame/gesture checks.
            tap(scroll_to('连接地址'));ui=wait_ui(lambda tree:any(n.attrib.get('class')=='android.widget.EditText' for n in tree.iter('node')),'connection dialog',reject_login=True)
            assert '连接地址' in items(ui), items(ui)
            assert any(n.attrib.get('class') == 'android.widget.EditText' for n in ui.iter('node')), items(ui)
            capture('android-connection.png')
            tap(named(ui, '取消'))
            tap(scroll_to('重新载入页面'));ui=wait_native('重新载入页面？')
            assert '重新载入页面？' in items(ui), items(ui)
            assert any('已保存的记录不受影响' in item for item in items(ui)), items(ui)
            tap(named(ui, '取消'))
            # Cached pages and native Back use the same document, connection and authenticated session.
            tap(named(screen(),'资产'));wait_page('/assets')
            adb('shell','input','keyevent','4');wait_page('/settings')
            assert document_requests()==initial_documents,('History reloaded the document',document_requests())
            # A real process death discards all in-memory passwords and WebViews.
            # Restart must use the persisted HttpOnly session cookie without a prompt.
            adb('shell','input','keyevent','3')
            time.sleep(1)
            adb('shell','am','force-stop','io.accufolio.app')
            adb('shell','am','start','-W','-n','io.accufolio.app/.MainActivity')
            ui=wait_page('/assets')
            assert document_requests()==initial_documents+['/assets'],document_requests()
            capture('android-reopened.png')
            tap(named(ui,'设置'));wait_page('/settings')
            tap(scroll_to('退出登录'))
            wait_ui(lambda tree:'欢迎回来' in items(tree),'explicit logout returns to login')
            print('Android real HTTPS navigation passed: five screens, separate net-assets and investments, one document, persistent login after force-stop/reopen, explicit logout, direct connection/reload actions.')
        except BaseException:
            try:
                capture('android-integration-failure.png')
                (WORK/'failure-ui.xml').write_text(ET.tostring(screen(),encoding='unicode'),encoding='utf-8')
            except Exception:pass
            raise
        finally:
            # Close the disposable WebView's keep-alive connections before shutting down HTTPS.
            try:
                subprocess.run(['adb', 'shell', 'am', 'force-stop', 'io.accufolio.app'],
                               check=False, capture_output=True, timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                print('Could not close the disposable app; continuing server cleanup.')
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'prepare':prepare()
    elif command == 'serve':serve()
    elif command == 'run':run(sys.argv[2])
    else:raise SystemExit('Expected prepare or run APK')
