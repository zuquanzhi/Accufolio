"""Device sessions remain valid across clients but expire and revoke reliably."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.main import app
from apps.api import auth


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ACCUFOLIO_DB': str(Path(self.temp.name) / 'test.db'),
                              'ACCUFOLIO_USER': 'admin', 'ACCUFOLIO_PASSWORD': 'session-test-secret',
                              'ACCUFOLIO_PUBLIC_ORIGIN': '',
                              'ACCUFOLIO_LOGIN_MAX_ATTEMPTS': '3', 'ACCUFOLIO_LOGIN_COOLDOWN': '30'})
        self.env.start()
        auth._attempts.clear()
        self.client = TestClient(app, base_url='https://testserver')
        self.headers = {'X-Accufolio': '1'}

    def tearDown(self):
        self.client.close()
        auth._attempts.clear()
        self.env.stop()
        self.temp.cleanup()

    def login(self, **overrides):
        return self.client.post('/api/session', headers=self.headers,
                                json={'username': 'admin', 'password': 'session-test-secret',
                                      'remember': True, **overrides})

    def test_persistent_cookie_and_new_client_with_same_database(self):
        result = self.login()
        self.assertEqual(result.status_code, 200)
        cookie = result.headers['set-cookie']
        for flag in ('HttpOnly', 'Secure', 'SameSite=strict', 'Max-Age=7776000'):
            self.assertIn(flag, cookie)
        token = self.client.cookies.get(auth.COOKIE)
        with auth.sessions() as con:
            row = con.execute('SELECT * FROM device_sessions').fetchone()
            self.assertEqual(row['token_hash'], auth.token_hash(token))
            self.assertNotIn(token, str(tuple(row)))
            self.assertNotIn('session-test-secret', str(tuple(row)))
        # A newly created HTTP client models a reopened app with its persisted cookie.
        with TestClient(app, base_url='https://testserver') as reopened:
            reopened.cookies.set(auth.COOKIE, token)
            self.assertEqual(reopened.get('/api/trades').status_code, 200)
            self.assertTrue(reopened.get('/api/session').json()['remembered'])
            self.assertEqual(reopened.get('/api/allocation').status_code, 200)
            self.assertEqual(reopened.get('/api/dashboard?include_history=false').status_code, 200)

    def test_browser_session_cookie_when_remember_unchecked(self):
        response = self.login(remember=False)
        first_cookie = response.headers.get_list('set-cookie')[0]
        self.assertNotIn('Max-Age', first_cookie)
        self.assertFalse(self.client.get('/api/session').json()['remembered'])
        with auth.sessions() as con:
            expiry = con.execute('SELECT expires_at FROM device_sessions').fetchone()[0]
        self.assertAlmostEqual(expiry - time.time(), auth.SESSION_SECONDS, delta=2)

    def test_logout_revokes_copied_token_and_basic_cache(self):
        self.login()
        token = self.client.cookies.get(auth.COOKIE)
        self.assertEqual(self.client.delete('/api/session').status_code, 403)
        self.assertEqual(self.client.delete('/api/session', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/trades', auth=('admin', 'session-test-secret')).status_code, 401)
        with TestClient(app, base_url='https://testserver') as stolen:
            stolen.cookies.set(auth.COOKIE, token)
            self.assertEqual(stolen.get('/api/trades').status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get('/api/trades').status_code, 200)

    def test_password_or_username_change_invalidates_session(self):
        for name, value in [('ACCUFOLIO_PASSWORD', 'changed-secret'), ('ACCUFOLIO_USER', 'changed-user')]:
            self.login()
            with patch.dict(os.environ, {name: value}):
                self.assertEqual(self.client.get('/api/trades').status_code, 401)

    def test_expired_or_forged_cookie_not_accepted(self):
        self.login()
        with auth.sessions() as con:
            con.execute('UPDATE device_sessions SET expires_at=0')
        self.assertEqual(self.client.get('/api/trades').status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set(auth.COOKIE, 'x' * 43)
        self.assertEqual(self.client.get('/api/trades').status_code, 401)

    def test_csrf_and_explicit_origin_validation(self):
        body = {'username': 'admin', 'password': 'session-test-secret'}
        self.assertEqual(self.client.post('/api/session', json=body).status_code, 403)
        self.assertEqual(self.client.post('/api/session', json=body,
                         headers={**self.headers, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/session', json=body,
                         headers={**self.headers, 'Origin': 'https://testserver'}).status_code, 200)

    def test_html_login_redirect_and_api_basic_compatibility(self):
        response = self.client.get('/assets', headers={'Accept': 'text/html'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/login?next=/assets')
        self.assertEqual(self.client.get('/api/trades').status_code, 401)
        response = self.client.get('/api/trades', auth=('admin', 'session-test-secret'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.client.get('/login').status_code, 200)
        self.assertIn('no-store', self.client.get('/login').headers['cache-control'])

    def test_expired_browser_fetch_does_not_open_native_basic_dialog(self):
        self.login()
        with auth.sessions() as con:
            con.execute('UPDATE device_sessions SET expires_at=0')
        response = self.client.get('/api/trades', headers=self.headers)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('WWW-Authenticate', response.headers)
        self.client.cookies.clear()
        self.assertIn('WWW-Authenticate', self.client.get('/api/trades').headers)

    def test_cookie_session_survives_legacy_basic_cache(self):
        self.login()
        self.assertEqual(self.client.get('/api/trades', auth=('admin', 'old-password')).status_code, 200)

    def test_rate_limit_shared_by_basic_and_form_and_not_spoofed_xff(self):
        with patch('apps.api.auth.time.monotonic', return_value=100):
            self.assertEqual(self.login(password='wrong').status_code, 401)
            self.assertEqual(self.client.get('/api/trades', auth=('admin', 'wrong')).status_code, 401)
            self.assertEqual(self.login(password='wrong').status_code, 401)
            blocked = self.client.post('/api/session', json={'username':'admin','password':'session-test-secret'},
                                      headers={**self.headers, 'X-Forwarded-For':'1.2.3.4'})
            self.assertEqual(blocked.status_code, 429)
            self.assertIn('Retry-After', blocked.headers)
        with patch('apps.api.auth.time.monotonic', return_value=131):
            self.assertEqual(self.login().status_code, 200)

    def test_success_resets_failed_attempts(self):
        for _ in range(2):
            self.assertEqual(self.login(password='wrong').status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        for _ in range(2):
            self.assertEqual(self.login(password='wrong').status_code, 401)
        self.assertEqual(self.login().status_code, 200)

    def test_new_login_rotates_and_revokes_prior_token(self):
        self.login()
        old = self.client.cookies.get(auth.COOKIE)
        self.login()
        self.assertNotEqual(old, self.client.cookies.get(auth.COOKIE))
        with auth.sessions() as con:
            self.assertIsNone(con.execute('SELECT * FROM device_sessions WHERE token_hash=?', (auth.token_hash(old),)).fetchone())

    def test_no_default_password(self):
        with patch.dict(os.environ, {'ACCUFOLIO_PASSWORD': ''}):
            self.assertEqual(self.login().status_code, 503)

    def test_explicit_https_proxy_origin_issues_secure_cookie_over_internal_http(self):
        with patch.dict(os.environ, {'ACCUFOLIO_PUBLIC_ORIGIN': 'https://invest.example.test'}):
            with TestClient(app, base_url='http://internal:8000') as internal:
                body = {'username':'admin','password':'session-test-secret'}
                response = internal.post('/api/session', json=body,
                                         headers={**self.headers, 'Origin':'https://invest.example.test'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('Secure', response.headers.get_list('set-cookie')[0])
                self.assertEqual(internal.post('/api/session', json=body,
                                 headers={**self.headers, 'Origin':'https://foreign.example.test'}).status_code, 403)
                # An arbitrary forwarded header cannot opt another origin in.
                self.assertEqual(internal.post('/api/session', json=body,
                                 headers={**self.headers, 'Origin':'https://foreign.example.test',
                                          'X-Forwarded-Host':'foreign.example.test', 'X-Forwarded-Proto':'https'}).status_code, 403)

    def test_invalid_public_origin_fails_closed(self):
        for origin in ['http://example.test', 'https://user:secret@example.test',
                       'https://example.test/path', 'https://example.test?query=1', 'https://example.test:bad']:
            with patch.dict(os.environ, {'ACCUFOLIO_PUBLIC_ORIGIN': origin}):
                response = self.login()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('secret', response.text)


if __name__ == '__main__':
    unittest.main()
