"""Real form login and browser-profile reopening without stored passwords."""
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


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'ACCUFOLIO_DB': str(Path(temp) / 'test.db'),
               'ACCUFOLIO_PASSWORD': 'browser-session-test', 'ACCUFOLIO_USER': 'admin',
               'ACCUFOLIO_PUBLIC_ORIGIN': ''}
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1',
                                   '--port', str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urlopen(base + '/manifest.webmanifest', timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Login test server did not start')
            with sync_playwright() as playwright:
                options = {'headless': True, 'viewport': {'width': 390, 'height': 844}}
                if os.name == 'nt':
                    options['channel'] = 'msedge'
                profile = str(Path(temp) / 'profile')
                context = playwright.chromium.launch_persistent_context(profile, **options)
                page = context.new_page()
                page.goto(base + '/records')
                expect(page.locator('#login-form')).to_be_visible()
                (ROOT / 'work').mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / 'work/login-preview.png'))
                page.locator('#password').fill('wrong')
                page.locator('#sign-in').click()
                expect(page.locator('#login-error')).to_contain_text('用户名或密码不正确')
                page.locator('#password').fill('browser-session-test')
                page.locator('#sign-in').click()
                expect(page).to_have_url(base + '/records')
                expect(page.locator('#view-records')).to_be_visible()
                cookies = context.cookies()
                token = next(item for item in cookies if item['name'] == 'accufolio_session')
                assert token['httpOnly'] and token['expires'] > time.time() + 80 * 86400, cookies
                assert page.evaluate('document.cookie').find(token['value']) == -1
                assert 'browser-session-test' not in page.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
                context.close()
                # Actually reopen the same disk-backed browser profile, not a shared HTTP client.
                context = playwright.chromium.launch_persistent_context(profile, **options)
                page = context.new_page()
                page.goto(base + '/assets')
                expect(page.locator('#view-assets')).to_be_visible()
                assert page.locator('#login-form').count() == 0
                page.get_by_role('link', name='设置', exact=True).click()
                expect(page.locator('#view-settings')).to_be_visible()
                result = page.evaluate("fetch('/api/session',{method:'DELETE',headers:{'X-Accufolio':'1'}}).then(r=>r.status)")
                assert result == 200
                page.goto(base + '/assets')
                expect(page.locator('#login-form')).to_be_visible()
                context.close()
            print('Login browser smoke passed: real form, remembered profile reopening, HttpOnly cookie, revocable logout.')
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == '__main__':
    main()
