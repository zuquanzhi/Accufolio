"""Read-only securities journal: combined filters, bounded rows, navigation and errors."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ROWS = [dict(id=i, account_id=1 if i <= 60 else 2, account_name='长期投资' if i <= 60 else '策略账户',
             currency='USD', fee=1, strategy='dca', symbol='AAA' if i <= 60 else 'BBB',
             shares=1, price=100, side='sell' if i % 2 == 0 else 'buy',
             trade_date='2026-11-13' if i <= 60 else '2026-10-12',
             note='工资定投' if i == 1 else '') for i in range(1, 76)]


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = {**os.environ, 'ACCUFOLIO_DB': str(Path(temp) / 'records.db'),
               'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'records-test'}
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--port', str(port)],
                                   cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urlopen(base + '/manifest.webmanifest', timeout=1).close(); break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Records test server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, **({'channel': 'msedge'} if os.name == 'nt' else {}))
                context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                assert context.request.post(base + '/api/session', data={'username': 'admin', 'password': 'records-test'}, headers={'X-Accufolio': '1'}).ok
                page = context.new_page(); errors = []; writes = []; state = {'fail': False}
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: writes.append(req.url) if req.method not in ('GET', 'HEAD') else None)
                page.route('**/api/trades', lambda route: route.fulfill(status=503 if state['fail'] else 200,
                           json={'detail': 'Records unavailable'} if state['fail'] else ROWS))
                page.goto(base + '/records')
                expect(page.locator('#record-count')).to_contain_text('75 条记录')
                expect(page.locator('#trades .trade-row')).to_have_count(50)
                expect(page.locator('#trades .trade-row').first).to_contain_text('卖出')  # same-date id descending
                page.locator('#record-more').click(); expect(page.locator('#trades .trade-row')).to_have_count(75)
                expect(page.locator('#record-more')).to_be_hidden()
                page.locator('#record-search').fill('  aaa  ')
                expect(page.locator('#record-count')).to_contain_text('60 条记录')
                page.locator('#record-filters summary').click()
                page.locator('#record-side').select_option('sell')
                expect(page.locator('#trades .trade-row')).to_have_count(30)
                page.locator('#record-month').select_option('2026-10')
                expect(page.locator('#trades')).to_contain_text('没有符合条件')
                page.locator('#record-clear').click()
                expect(page.locator('#trades .trade-row')).to_have_count(50)
                page.locator('#record-account').select_option('2')
                page.locator('#record-month').select_option('2026-10')
                expect(page.locator('#trades .trade-row')).to_have_count(15)
                assert not page.locator('#trades').get_by_text('AAA', exact=True).count()
                page.evaluate('window.recordDocumentMarker=42')
                page.locator('[data-view=discover]').click(); expect(page.locator('#discover-home')).to_be_visible()
                page.locator('[data-view=records]').click()
                expect(page.locator('#record-account')).to_have_value('2')
                expect(page.locator('#trades .trade-row')).to_have_count(15)
                assert page.evaluate('window.recordDocumentMarker') == 42
                page.locator('#record-clear').click(); page.locator('#record-search').fill('工资定投')
                expect(page.locator('#trades .trade-row')).to_have_count(1)
                page.locator('#record-search').fill('<img src=x onerror=alert(1)>')
                expect(page.locator('#trades')).to_contain_text('没有符合条件')
                assert not page.locator('#trades img').count()
                for width in [320, 390, 1280]:
                    page.set_viewport_size({'width': width, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.locator('#record-clear').click(); state['fail'] = True
                page.evaluate('invalidateData(); activeEntry.controller.activate()')
                expect(page.locator('#record-count')).to_have_text('记录暂不可用')
                expect(page.locator('#trades .trade-row')).to_have_count(0)
                expect(page.locator('#record-more')).to_be_hidden()
                state['fail'] = False
                page.evaluate('invalidateData(); activeEntry.controller.activate()')
                expect(page.locator('#trades .trade-row')).to_have_count(50)
                expect(page.locator('#notice')).to_have_text('')
                assert not errors, errors
                assert not writes, writes
                browser.close()
        finally:
            process.terminate(); process.wait(timeout=15)
    print('Records checks passed: combined search/account/side/month filters, stable order, progressive rows, preserved navigation, failure recovery, responsive layout and no writes.')


if __name__ == '__main__':
    main()
