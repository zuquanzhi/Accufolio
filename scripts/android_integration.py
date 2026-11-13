"""Exercise the real app over local HTTPS using an opt-in, test-only debug CA.

prepare: make an ephemeral CA/certificate before -PandroidIntegration=true builds.
run APK: serve the actual FastAPI app and drive the original MainActivity by touch.
No fixtures, CA, alternate WebViewClient, or relaxed TLS policy enter the release APK.
"""
import os
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
    from datetime import date
    import uvicorn
    import apps.api.main as api
    import apps.api.extended as monitoring
    from packages.providers import Quote, QuoteResult
    quote = lambda symbol: QuoteResult(Quote(symbol, 204.75, .84, 'test', 0), False, ())
    api.get_quote = quote
    monitoring.get_quote = quote
    api.mutate_trade('add', api.Trade(symbol='QQQM', trade_date=date(2025, 1, 2), shares=2, price=180), None, 'integration')

    @api.app.middleware('http')
    async def log_navigation(request, call_next):
        response = await call_next(request)
        if request.url.path in ('/assets', '/records', '/discover', '/settings') and response.status_code == 200:
            with NAV.open('a', encoding='utf-8') as stream:
                stream.write(request.url.path + '\n')
        return response

    uvicorn.run(api.app, host='0.0.0.0', port=PORT, ssl_keyfile=str(KEY), ssl_certfile=str(CERT), log_level='warning')


def adb(*args):
    return subprocess.check_output(['adb', *args], text=True)


def screen():
    adb('shell', 'uiautomator', 'dump', '/sdcard/accufolio-ui.xml')
    return ET.fromstring(adb('shell', 'cat', '/sdcard/accufolio-ui.xml'))


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


def routes():
    return NAV.read_text(encoding='utf-8').splitlines() if NAV.exists() else []


def wait_route(route, start):
    for _ in range(50):
        if route in routes()[start:]:
            time.sleep(.7)
            ui = screen()
            assert '应用设置' not in items(ui), items(ui)
            return ui
        time.sleep(.2)
    raise AssertionError(('No actual HTTP navigation', route, routes()[start:], items(screen())))


def scroll_to(value):
    width, height = list(map(int, re.findall(r'\d+', adb('shell', 'wm', 'size'))))[-2:]
    for _ in range(8):
        ui = screen()
        matches = [n for n in ui.iter('node') if text(n) == value]
        for node in matches:
            x1, y1, x2, y2 = bounds(node)
            if x2 > x1 and y2 > y1 and y1 > 30 and y2 < height-95:
                return node
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
            for _ in range(20):
                ui = screen()
                if '登录' in items(ui):
                    break
                time.sleep(.3)
            password = [n for n in ui.iter('node') if n.attrib.get('class') == 'android.widget.EditText'][-1]
            tap(password);adb('shell', 'input', 'text', 'android-integration-test')
            tap(named(screen(), '登录'))
            ui = wait_route('/assets', 0)
            capture('android-assets.png')
            for label, route in [('记录','/records'),('发现','/discover'),('设置','/settings')]:
                start = len(routes());tap(named(ui, label));ui = wait_route(route, start)
            capture('android-settings-page.png')
            # These taps are real input events, exercising MainActivity's origin/frame/gesture checks.
            tap(scroll_to('连接地址'));ui=screen()
            assert '连接地址' in items(ui), items(ui)
            assert any(n.attrib.get('class') == 'android.widget.EditText' for n in ui.iter('node')), items(ui)
            capture('android-connection.png')
            tap(named(ui, '取消'))
            tap(scroll_to('重新载入页面'));ui=screen()
            assert '重新载入页面？' in items(ui), items(ui)
            assert any('已保存的记录不受影响' in item for item in items(ui)), items(ui)
            tap(named(ui, '取消'))
            # Real browser history returns through previously fetched documents, without a second native bar.
            start=len(routes());tap(named(screen(), '资产'));wait_route('/assets',start)
            adb('shell', 'input', 'keyevent', '4');time.sleep(1)
            assert '应用设置' not in items(screen()), items(screen())
            print('Android real HTTPS pages passed: assets/records/discover/settings, no duplicate native bar, connection and reload actions by touch.')
        finally:
            server.terminate();server.wait(timeout=15)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'prepare':prepare()
    elif command == 'serve':serve()
    elif command == 'run':run(sys.argv[2])
    else:raise SystemExit('Expected prepare or run APK')
