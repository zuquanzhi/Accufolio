"""Account detail integrity in a real browser, using an isolated DB and mocked reads."""
from copy import deepcopy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RATES = {'base': 'USD', 'rates': {'USD': 1, 'HKD': 7.8, 'CNY': 7.2},
         'source': 'fixture', 'as_of': '2026-11-13', 'available': True, 'stale': False}
ACCOUNTS = [
    {'id': 1, 'name': 'Securities', 'kind': 'broker', 'purpose': 'investing', 'currency': 'USD',
     'institution': 'Test Broker', 'balance': 0, 'as_of': '2026-11-04', 'value_usd': 400,
     'cash_value_usd': 0, 'securities_value_usd': 400, 'quote_timestamp_min': 1794603600, 'revision': 2, 'entry_count': 2},
    {'id': 2, 'name': 'Savings', 'kind': 'bank', 'purpose': 'savings', 'currency': 'HKD',
     'institution': 'Test Bank', 'balance': 4680, 'as_of': '2026-11-04', 'value_usd': 600,
     'cash_value_usd': 600, 'securities_value_usd': 0, 'revision': 5, 'entry_count': 5},
]
ENTRIES = {
    1: [
        {'id': 15, 'entry_date': '2026-11-04', 'kind': 'fx_out', 'amount': -100,
         'currency': 'USD', 'counter_account_id': 2},
        {'id': 5, 'entry_date': '2026-11-01', 'kind': 'balance_open', 'amount': 100, 'currency': 'USD'},
    ],
    2: [
        {'id': 14, 'entry_date': '2026-11-04', 'kind': 'fee', 'amount': -100, 'currency': 'HKD'},
        {'id': 13, 'entry_date': '2026-11-04', 'kind': 'fx_in', 'amount': 780,
         'currency': 'HKD', 'counter_account_id': 1},
        {'id': 12, 'entry_date': '2026-11-04', 'kind': 'deposit', 'amount': 200,
         'currency': 'HKD', 'reverses_entry_id': 11, 'note': '人工更正'},
        {'id': 11, 'entry_date': '2026-11-03', 'kind': 'withdraw', 'amount': -200,
         'currency': 'HKD', 'note': '错误金额'},
        {'id': 10, 'entry_date': '2026-11-01', 'kind': 'balance_open', 'amount': 4000, 'currency': 'HKD'},
    ],
}


def main():
    with tempfile.TemporaryDirectory() as temp:
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        env = {**os.environ, 'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'accounts-test',
               'ACCUFOLIO_DB': str(Path(temp) / 'accounts.db')}
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--port', str(port),
                                   '--timeout-graceful-shutdown', '3'], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urlopen(base + '/manifest.webmanifest', timeout=1).close(); break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Test server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, **({'channel': 'msedge'} if os.name == 'nt' else {}))
                context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                assert context.request.post(base + '/api/session', data={
                    'username': 'admin', 'password': 'accounts-test', 'remember': True}, headers={'X-Accufolio': '1'}).ok
                page = context.new_page(); errors = []; writes = []; pending = []
                state = {'fail': False, 'hold': None, 'accounts': deepcopy(ACCOUNTS), 'journals': deepcopy(ENTRIES),
                         'entry_status': 200, 'mismatch': None}
                entry_requests = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)
                def assets(route):
                    if state['fail']:
                        route.fulfill(status=503, json={'detail': 'Unavailable'}); return
                    route.fulfill(json={'accounts': state['accounts'], 'net_assets_usd': 1000, 'cash_value_usd': 600,
                                        'securities_value_usd': 400, 'complete': True, 'fx': RATES, 'warnings': []})
                page.route('**/api/assets', assets)
                page.route('**/api/allocation', lambda route: route.fulfill(json={'securities': {}, 'net_worth': {}}))
                page.route('**/api/fx', lambda route: route.fulfill(json=RATES))
                page.route('**/api/version', lambda route: route.fulfill(json={'version': 'accounts-fixture'}))
                def entries(route):
                    account = int(route.request.url.split('/')[-2])
                    expected = int(parse_qs(urlsplit(route.request.url).query)['expected_revision'][0])
                    entry_requests.append((account, expected))
                    if state['entry_status'] != 200:
                        route.fulfill(status=state['entry_status'], json={'detail': 'Read a fresh account snapshot'}); return
                    if expected != next(row['revision'] for row in state['accounts'] if row['id'] == account):
                        route.fulfill(status=409, json={'detail': 'Account changed'}); return
                    reply = deepcopy(state['journals'][account])
                    if state['mismatch'] == 'balance': reply[0]['amount'] += 1
                    if state['mismatch'] == 'count': reply.append({**reply[-1], 'id': 999, 'amount': 0})
                    if state['hold'] == account:
                        pending.append((route, reply)); return
                    route.fulfill(json=reply)
                page.route('**/api/accounts/*/cash-entries?*', entries)
                def row(entry_id):
                    return page.locator(f'#account-cash-entries [data-entry-id="{entry_id}"]')
                def open_account(account):
                    page.locator(f'#asset-accounts [data-account-id="{account}"]').click()
                    page.wait_for_url(base + '/assets?account=' + str(account))
                def wait_pending():
                    for _ in range(100):
                        if pending: return
                        page.wait_for_timeout(20)
                    raise AssertionError('Expected held account entries')
                def release_pending():
                    for route, reply in pending: route.fulfill(json=reply)
                    pending.clear(); page.wait_for_timeout(100)

                page.goto(base + '/assets'); expect(page.locator('#assets-total')).to_have_text('$1,000.00')
                open_account(2)
                expect(page.locator('#account-securities-metric')).to_be_hidden()
                expect(page.locator('#account-cash-label')).to_have_text('原币余额')
                expect(page.locator('#account-value-note')).to_have_text('账户余额折合 · USD')
                expect(row(11)).to_contain_text('转出 · 已冲销')
                expect(row(11)).to_contain_text('由流水 #12 冲销')
                expect(row(12)).to_contain_text('冲销流水 #11')
                expect(row(12)).to_contain_text('人工更正')
                expect(row(13)).to_contain_text('来自 Securities')
                for entry_id, balance in [(10, '4,000.00'), (11, '3,800.00'), (12, '4,000.00'), (13, '4,780.00'), (14, '4,680.00')]:
                    expect(row(entry_id).locator('.cash-entry-balance')).to_have_text('余额 HK$' + balance)
                assert page.locator('#account-cash-entries [data-entry-id]').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.entryId))') == [14, 13, 12, 11, 10]
                page.evaluate("setDisplayCurrency('CNY')")
                expect(page.locator('#account-value')).to_contain_text('4,320.00')
                expect(row(14).locator('.cash-entry-balance')).to_have_text('余额 HK$4,680.00')
                page.set_viewport_size({'width': 320, 'height': 740})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Account detail overflows small phone'

                # A previous account's delayed journal must never overwrite the next account.
                state['hold'] = 2; page.evaluate('invalidateData()'); wait_pending()
                page.locator('#account-back').click(); page.wait_for_url(base + '/assets'); open_account(1)
                expect(row(15)).to_contain_text('转至 Savings')
                expect(row(15).locator('.cash-entry-balance')).to_have_text('余额 $0.00')
                release_pending()
                expect(row(10)).to_have_count(0)
                expect(page.locator('#account-kind')).to_contain_text('Test Broker')
                expect(page.locator('#account-securities-metric')).to_be_visible()
                expect(page.locator('#account-cash-label')).to_have_text('账户现金')
                expect(page.locator('#account-value-note')).to_contain_text('证券市值与账户现金合计')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Broker detail overflows small phone'

                # Unknown details clear all labels, including those from the previous account.
                page.evaluate("history.pushState({page:'assets'},'', '/assets?account=999');navigatePage('assets',{push:false})")
                expect(page.locator('#account-value')).to_have_text('—')
                expect(page.locator('#account-kind')).to_have_text('账户')
                expect(page.locator('#account-securities-metric')).to_be_hidden()
                for field in ['account-purpose', 'account-balance-date', 'account-quote-time']:
                    expect(page.locator('#' + field)).to_be_empty()
                expect(page.locator('#account-cash-entries')).to_contain_text('当前不可查看')
                page.evaluate("history.replaceState({page:'assets'},'', '/assets?account=2');navigatePage('assets',{push:false})")
                state['hold'] = None; release_pending()
                expect(row(14)).to_be_visible()

                # A failed account refresh invalidates a concurrent journal response as well.
                state['hold'] = 2; state['fail'] = True; page.evaluate('invalidateData()'); wait_pending()
                expect(page.locator('#account-value-note')).to_contain_text('已清空上次估值')
                for field in ['account-value', 'account-native-balance', 'account-securities-value']:
                    expect(page.locator('#' + field)).to_have_text('—')
                expect(page.locator('#account-cash-entries')).to_contain_text('暂不可用')
                release_pending(); expect(row(14)).to_have_count(0)
                state['hold'] = None; state['fail'] = False; page.evaluate('invalidateData()')
                expect(row(14)).to_be_visible()
                expect(page.locator('#account-native-balance')).to_have_text('HK$4,680.00')
                expect(page.locator('#account-securities-metric')).to_be_hidden()

                # Agent writes do not increment the browser's dataRevision. A fresh
                # summary must still replace a recently cached, older journal.
                page.clock.install()
                page.goto(base + '/assets'); expect(page.locator('#assets-total')).to_have_text('CN¥7,200.00')
                page.clock.fast_forward(40_000); open_account(2); expect(row(14)).to_be_visible()
                browser_revision = page.evaluate('dataRevision')
                def agent_deposit(entry_id):
                    account = state['accounts'][1]
                    account.update(revision=account['revision'] + 1, entry_count=account['entry_count'] + 1,
                                   balance=account['balance'] + 100, as_of='2026-11-05')
                    account['cash_value_usd'] = account['value_usd'] = account['balance'] / 7.8
                    state['journals'][2].insert(0, {'id': entry_id, 'entry_date': '2026-11-05', 'kind': 'deposit',
                                                  'amount': 100, 'currency': 'HKD', 'note': 'Agent 写入'})
                agent_deposit(20)
                page.clock.fast_forward(20_000)
                expect(page.locator('#account-native-balance')).to_have_text('HK$4,780.00')
                expect(row(20).locator('.cash-entry-balance')).to_have_text('余额 HK$4,780.00')
                assert page.evaluate('dataRevision') == browser_revision
                assert entry_requests[-1] == (2, 6), entry_requests

                # A reply read at revision 7 may arrive after revision 8 is on screen.
                state['hold'] = 2; agent_deposit(21); page.clock.fast_forward(60_000); wait_pending()
                expect(page.locator('#account-native-balance')).to_have_text('HK$4,880.00')
                expect(row(20)).to_have_count(0)
                state['hold'] = None; agent_deposit(22); page.clock.fast_forward(60_000)
                expect(row(22).locator('.cash-entry-balance')).to_have_text('余额 HK$4,980.00')
                release_pending()
                expect(row(22).locator('.cash-entry-balance')).to_have_text('余额 HK$4,980.00')
                assert page.evaluate('dataRevision') == browser_revision

                for mismatch in ['balance', 'count']:
                    state['mismatch'] = mismatch; page.clock.fast_forward(60_000)
                    expect(page.locator('#account-cash-entries')).to_contain_text('流水与账户数据暂未同步')
                    expect(page.locator('#account-cash-entries [data-entry-id]')).to_have_count(0)
                    count = len(entry_requests)
                    page.evaluate('refreshPreferences();refreshPreferences()')
                    page.wait_for_timeout(50)
                    assert len(entry_requests) == count, 'Invalid journal triggered an immediate retry loop'
                    state['mismatch'] = None; page.clock.fast_forward(60_000)
                    expect(row(22).locator('.cash-entry-balance')).to_have_text('余额 HK$4,980.00')
                state['entry_status'] = 409; page.clock.fast_forward(60_000)
                expect(page.locator('#account-cash-entries')).to_contain_text('流水与账户数据暂未同步')
                expect(row(22)).to_have_count(0)
                state['entry_status'] = 200; page.clock.fast_forward(60_000)
                expect(row(22)).to_be_visible()
                assert not writes, writes
                assert not errors, errors
                browser.close()
            print('Account browser checks passed: native running balances, reversal links, transfer counterparties, failure clearing and account-response isolation.')
        finally:
            server.terminate()
            try: server.wait(timeout=8)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)


if __name__ == '__main__':
    main()
