"""Asset-history navigation, currency-aware returns, gaps and cash-dividend presentation."""
from copy import deepcopy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from dashboard_smoke import payload, TRADES, PLAN
from accounts_smoke import ACCOUNTS, ENTRIES, RATES

ROOT = Path(__file__).resolve().parents[1]


def history_payload(currency='USD', incomplete=False):
    values = [1000, 1100, 990] if currency == 'USD' else [7000, 7920, 7029]
    navs = [v / values[0] for v in values]
    return dict(currency=currency, start='2026-11-10', end='2026-11-12', as_of='2026-11-13',
                series=[dict(day=day, value=value, nav=None if incomplete else nav,
                             drawdown=None if incomplete else nav / max(navs[:i+1]) - 1)
                        for i, (day, value, nav) in enumerate(zip(['2026-11-10', '2026-11-11', '2026-11-12'], values, navs))],
                summary=dict(twr=None if incomplete else navs[-1] - 1,
                             max_drawdown=None if incomplete else navs[-1] / navs[1] - 1,
                             drawdown_date=None if incomplete else '2026-11-12',
                             gain=None if incomplete else values[-1] - values[0], net_external_flow=0),
                coverage=dict(complete=not incomplete, performance_complete=not incomplete,
                              valuation_complete=not incomplete, prices_complete=True,
                              unmatched_trade_ids=[7, 8] if incomplete and incomplete != 'invalid' else [],
                              issues=[dict(code='invalid_settlement', message='结算关联无效')] if incomplete == 'invalid' else []),
                warnings=['交易结算尚未核对'] if incomplete else [],
                method_note='按实际账户现金、证券与历史汇率估值。净值剔除外部资金流。')


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = {**os.environ, 'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'returns-test',
               'ACCUFOLIO_DB': str(Path(temp) / 'returns.db')}
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--port', str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urlopen(base + '/manifest.webmanifest', timeout=1).close(); break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Returns server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, **({'channel': 'msedge'} if os.name == 'nt' else {}))
                context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                assert context.request.post(base + '/api/session', data={'username': 'admin', 'password': 'returns-test'}, headers={'X-Accufolio': '1'}).ok
                page = context.new_page(); errors = []; writes = []; history_calls = []; pending = []
                state = {'incomplete': False, 'fail': False, 'hold': False, 'dividend_issue': False,
                         'missing_valuation': None}
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: writes.append(req.url) if req.method not in ('GET', 'HEAD') else None)
                page.route('**/api/fx', lambda r: r.fulfill(json=RATES))
                def assets(route):
                    accounts = deepcopy(ACCOUNTS)
                    incomplete = bool(state['incomplete'])
                    accounts[0]['settlement_complete'] = not incomplete
                    accounts[1]['settlement_complete'] = True
                    accounts[0]['value_usd'] = 390 if incomplete else 400
                    missing = state['missing_valuation']
                    route.fulfill(json=dict(accounts=accounts, net_assets_usd=None if incomplete or missing else 1000,
                                             recorded_assets_usd=None if missing else 990 if incomplete else 1000,
                                             recorded_complete=not missing, settlement_complete=not incomplete,
                                             unmatched_trade_ids=[7, 8] if incomplete else [],
                                             cash_value_usd=600, securities_value_usd=390 if incomplete else 400,
                                             complete=not incomplete and not missing, fx=RATES, warnings=[],
                                             missing_quotes=['AAA'] if missing == 'quote' else [],
                                             missing_fx=['HKD'] if missing == 'fx' else []))

                page.route('**/api/assets', assets)
                page.route('**/api/accounts/*/cash-entries?*', lambda r: r.fulfill(
                    json=ENTRIES[int(urlsplit(r.request.url).path.split('/')[3])]))
                page.route('**/api/allocation', lambda r: r.fulfill(json=PLAN))
                page.route('**/api/trades', lambda r: r.fulfill(json=TRADES))
                page.route('**/api/fx/history?*', lambda r: r.fulfill(json={'points': [], 'stale': False}))

                def wealth(route):
                    query = parse_qs(urlsplit(route.request.url).query); history_calls.append(query)
                    reply = history_payload(query['currency'][0], state['incomplete'])
                    if state['hold']:
                        pending.append((route, reply)); return
                    route.fulfill(status=503 if state['fail'] else 200, json={'detail': 'Unavailable'} if state['fail'] else reply)

                page.route('**/api/assets/history?*', wealth)

                def investment(route):
                    args = parse_qs(urlsplit(route.request.url).query)
                    result = payload(args.get('period', ['1y'])[0], args.get('include_history', ['true'])[0] != 'false')
                    result['summary'].update(cash_dividends=12.5, dividends_complete=not state['dividend_issue'],
                                             total_return=None if state['dividend_issue'] else result['summary']['total_pnl'] + 12.5)
                    if state['dividend_issue']: result['summary'].update(xirr=None, xirr_reason='分红归属未核对')
                    result.update(account_names={'1': '长期投资'},
                                  dividend_breakdown=[dict(symbol='CLOSED', cash_dividends=12.5, accounts=[dict(account_id=1, cash_dividends=12.5)])],
                                  dividend_events=[dict(symbol='CLOSED', account_id=1, day='2026-10-01', amount=15, entry_id=9),
                                                   dict(symbol='CLOSED', account_id=1, day='2026-10-02', amount=-2.5, entry_id=10, reverses_entry_id=8)],
                                  dividend_issues=[dict(entry_id=11, account_id=1, symbol=None, reason='分红尚未填写标的')] if state['dividend_issue'] else [])
                    route.fulfill(json=result)

                page.route('**/api/dashboard?*', investment)
                page.goto(base + '/assets'); expect(page.locator('#assets-total')).to_have_text('$1,000.00')
                assert not history_calls, 'Home must not load analysis before it is opened'
                page.evaluate('window.returnsDocumentMarker=17')
                page.locator('#open-asset-history').click(); page.wait_for_url(base + '/assets?detail=history')
                expect(page.locator('#wealth-twr')).to_have_text('-1.00%')
                expect(page.locator('#wealth-drawdown')).to_have_text('-10.00%')
                expect(page.locator('#wealth-gain')).to_have_text('-$10.00')
                expect(page.locator('#assets-overview')).to_be_hidden()
                page.locator('[data-wealth-curve=nav]').click()
                expect(page.locator('#asset-history-chart')).to_contain_text('资金流调整')
                page.locator('[data-wealth-curve=drawdown]').click()
                expect(page.locator('#asset-history-chart .chart-value strong')).to_have_text('-10.00%')
                assert any('%' in text for text in page.locator('#asset-history-chart .chart-axis').all_text_contents())
                page.evaluate('refreshPreferences()')
                expect(page.locator('#wealth-twr')).to_have_text('-1.00%')
                assert page.evaluate('accufolioBack()')
                page.wait_for_url(base + '/assets'); expect(page.locator('#asset-history-detail')).to_be_hidden()
                page.locator('#open-asset-history').click()
                expect(page.locator('[data-wealth-curve=drawdown]')).to_have_attribute('aria-pressed', 'true')
                state['incomplete'] = True; page.evaluate('invalidateData()')
                expect(page.locator('#wealth-twr')).to_have_text('—')
                expect(page.locator('#wealth-drawdown')).to_have_text('—')
                expect(page.locator('#asset-history-status')).to_contain_text('不可计算')
                page.locator('#wealth-method summary').click()
                expect(page.locator('#wealth-warnings')).to_contain_text('交易 #7、#8')
                expect(page.locator('#asset-history-chart')).to_contain_text('数据尚不足')
                page.locator('[data-wealth-curve=value]').click()
                expect(page.locator('#asset-history-chart .chart-line')).to_have_count(1)
                expect(page.locator('[data-wealth-curve=value]')).to_have_text('记录估值')
                expect(page.locator('#asset-history-chart')).to_contain_text('已记录资产估值')
                expect(page.locator('#asset-history-chart')).not_to_contain_text('净资产（历史估值）')
                expect(page.locator('#asset-history-status')).to_contain_text('不能视为完整净资产')
                # Current and historical values share the same settlement qualification.
                page.locator('#account-back').click(); page.wait_for_url(base + '/assets')
                expect(page.locator('#assets-total')).to_have_text('$990.00')
                expect(page.locator('#assets-total-label')).to_have_text('已记录资产估值 · USD')
                expect(page.locator('.net-assets-card')).to_have_attribute('aria-label', '已记录资产估值')
                expect(page.locator('#assets-total-note')).to_contain_text('未知应收应付款尚未确认')
                expect(page.locator('#assets-total-note')).to_contain_text('Agent 补充结算关联：交易 #7、#8')
                expect(page.locator('#total-allocation-ring svg')).to_have_count(0)
                expect(page.locator('#total-allocation-ring')).to_contain_text('暂不计算整体占比与目标差额')
                expect(page.locator('#total-allocation-legend [data-account-id="1"] strong')).to_have_text('—')
                expect(page.locator('#total-allocation-legend')).to_contain_text('目标 50%')
                expect(page.locator('#total-allocation-legend')).not_to_contain_text('差额')
                page.locator('#asset-accounts [data-account-id="1"]').click()
                expect(page.locator('#account-value-note')).to_contain_text('记录估值 · 结算待核对')
                page.locator('#account-back').click(); page.wait_for_url(base + '/assets')
                page.locator('#asset-accounts [data-account-id="2"]').click()
                expect(page.locator('#account-value-note')).to_have_text('账户余额折合 · USD')
                page.locator('#account-back').click(); page.wait_for_url(base + '/assets')
                # Missing prices or FX must never expose a partial recorded aggregate.
                for missing, expected in [('quote', '股票行情'), ('fx', 'HKD 汇率')]:
                    state['missing_valuation'] = missing; page.evaluate('invalidateData()')
                    expect(page.locator('#assets-total')).to_have_text('—')
                    expect(page.locator('#assets-total-note')).to_contain_text(expected)
                state['missing_valuation'] = None; page.evaluate('invalidateData()')
                expect(page.locator('#assets-total')).to_have_text('$990.00')
                page.locator('#open-asset-history').click()
                expect(page.locator('#asset-history-chart .chart-value strong')).to_have_text('$990.00')
                # Invalid associations need the same qualification even with no unmatched ids.
                state['incomplete'] = 'invalid'; page.evaluate('invalidateData()')
                expect(page.locator('#asset-history-chart')).to_contain_text('已记录资产估值')
                expect(page.locator('#asset-history-status')).to_contain_text('待核对结算')
                expect(page.locator('#wealth-gain')).to_have_text('—')

                # A late USD result cannot overwrite a newly chosen CNY calculation.
                state['incomplete'] = False; state['hold'] = True
                page.evaluate('invalidateData()')
                page.locator('[data-wealth-period="3m"]').click()
                page.wait_for_timeout(100); assert pending
                state['hold'] = False; page.evaluate("setDisplayCurrency('CNY')")
                expect(page.locator('#wealth-twr')).to_have_text('0.41%')
                expect(page.locator('#wealth-gain')).to_have_text('CN¥29.00')
                for route, reply in pending: route.fulfill(json=reply)
                pending.clear(); page.wait_for_timeout(100)
                expect(page.locator('#wealth-twr')).to_have_text('0.41%')
                expect(page.locator('#asset-history-currency')).to_have_text('· CNY')
                expect(page.locator('[data-wealth-curve=value]')).to_have_text('净资产')
                expect(page.locator('#asset-history-chart')).to_contain_text('净资产（历史估值）')
                expect(page.locator('#assets-total-label')).to_have_text('净资产 · CNY')
                expect(page.locator('#total-allocation-ring svg')).to_have_count(1)
                expect(page.locator('#total-allocation-legend')).to_contain_text('差额')
                for width in [320, 390, 1280]:
                    page.set_viewport_size({'width': width, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                state['fail'] = True; page.evaluate('invalidateData()')
                expect(page.locator('#retry-asset-history')).to_be_visible()
                expect(page.locator('#wealth-gain')).to_have_text('—')
                expect(page.locator('#asset-history-chart .chart-line')).to_have_count(0)
                state['fail'] = False; page.locator('#retry-asset-history').click()
                expect(page.locator('#wealth-gain')).to_have_text('CN¥29.00')
                assert page.evaluate('window.returnsDocumentMarker') == 17

                page.evaluate("setDisplayCurrency('USD')")
                page.locator('[data-view=investments]').click()
                page.locator('[data-asset-detail=performance]').click()
                expect(page.locator('#investment-total-return')).to_have_text('$94.50')
                expect(page.locator('#investment-dividends')).to_have_text('$12.50')
                page.locator('#dividend-detail > summary').click()
                expect(page.locator('#dividend-breakdown')).to_contain_text('当前已无持仓')
                page.locator('.dividend-security > summary').click()
                expect(page.locator('#dividend-breakdown')).to_contain_text('长期投资')
                expect(page.locator('#dividend-breakdown')).to_contain_text('冲销 #8')
                state['dividend_issue'] = True; page.evaluate('invalidateData()')
                expect(page.locator('#investment-total-return')).to_have_text('—')
                expect(page.locator('#xirr')).to_have_text('—')
                expect(page.locator('#investment-dividends')).to_have_text('$12.50')
                expect(page.locator('#dividend-coverage')).to_contain_text('下方仅列已归属金额')
                expect(page.locator('#dividend-breakdown')).to_contain_text('分红尚未填写标的')
                page.set_viewport_size({'width': 320, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                assert not writes, writes
                browser.close()
        finally:
            server.terminate(); server.wait(timeout=15)
    print('Return UI checks passed: lazy history, dedicated routes/back, NAV/drawdown gaps, currency races, failures, dividend attribution and closed holdings, responsive read-only views.')


if __name__ == '__main__': main()
