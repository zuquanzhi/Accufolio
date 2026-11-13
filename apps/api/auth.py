"""Revocable device sessions; passwords never leave the login request or env."""
from contextlib import contextmanager
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
COOKIE = 'accufolio_session'
LOGGED_OUT = 'accufolio_logged_out'
PERSIST_SECONDS = 90 * 24 * 60 * 60
SESSION_SECONDS = 24 * 60 * 60
security = HTTPBasic(auto_error=False)
router = APIRouter()
_attempts = OrderedDict()
_attempts_lock = threading.Lock()


def public_origin():
    """Explicit deployment origin, rather than trusting arbitrary proxy headers."""
    configured = os.environ.get('ACCUFOLIO_PUBLIC_ORIGIN', '').strip()
    if not configured:
        return None
    try:
        value = urlsplit(configured)
        if (value.scheme != 'https' or not value.hostname or value.username is not None
                or value.password is not None or value.path not in ('', '/')
                or value.query or value.fragment or value.port == 0
                or any(ord(char) <= 32 for char in configured)):
            raise ValueError()
        hostname = value.hostname.encode('idna').decode('ascii').lower()
        host = '[' + hostname + ']' if ':' in hostname else hostname
        return 'https://' + host + (f':{value.port}' if value.port not in (None, 443) else '')
    except (ValueError, UnicodeError):
        raise HTTPException(503, 'ACCUFOLIO_PUBLIC_ORIGIN must be an HTTPS origin without a path') from None


def secure_cookie(request):
    return public_origin() is not None or request.url.scheme == 'https'


def auth_attempt(request, valid=None):
    """Bounded process-local throttle; use the ASGI peer, never arbitrary XFF."""
    key = request.client.host if request.client else 'unknown'
    maximum = max(1, int(os.environ.get('ACCUFOLIO_LOGIN_MAX_ATTEMPTS', '10')))
    cooldown = max(1, int(os.environ.get('ACCUFOLIO_LOGIN_COOLDOWN', '300')))
    now = time.monotonic()
    with _attempts_lock:
        count, until = _attempts.get(key, (0, now + cooldown))
        if until <= now:
            count, until = 0, now + cooldown
        if count >= maximum:
            raise HTTPException(429, 'Too many login attempts; try again later',
                                headers={'Retry-After': str(max(1, int(until - now) + 1))})
        if valid is True:
            _attempts.pop(key, None)
        elif valid is False:
            _attempts[key] = (count + 1, now + cooldown if count + 1 >= maximum else until)
            _attempts.move_to_end(key)
            while len(_attempts) > 4096:
                _attempts.popitem(last=False)


def configured_credentials():
    username = os.environ.get('ACCUFOLIO_USER', 'admin')
    password = os.environ.get('ACCUFOLIO_PASSWORD', '')
    if not password:
        raise HTTPException(503, 'Set ACCUFOLIO_PASSWORD before starting Accufolio')
    return username, password


def credential_fingerprint():
    username, password = configured_credentials()
    return hmac.new(password.encode(), b'Accufolio session v1\0' + username.encode(), hashlib.sha256).hexdigest()


def check_credentials(username, password):
    expected_user, expected_password = configured_credentials()
    return (hmac.compare_digest(username.encode(), expected_user.encode()) &
            hmac.compare_digest(password.encode(), expected_password.encode()))


@contextmanager
def sessions():
    path = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT / 'data' / 'accufolio.db')))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute('''CREATE TABLE IF NOT EXISTS device_sessions(
            token_hash TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            expires_at INTEGER NOT NULL, remembered INTEGER NOT NULL)''')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_session(request):
    token = request.cookies.get(COOKIE, '')
    if len(token) != 43:
        return None
    with sessions() as con:
        row = con.execute('SELECT * FROM device_sessions WHERE token_hash=?', (token_hash(token),)).fetchone()
    if row and row['expires_at'] > time.time() and hmac.compare_digest(row['fingerprint'], credential_fingerprint()):
        return dict(row)
    return None


def authorize(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
    username, _ = configured_credentials()
    # A logout marker also prevents a browser's old Basic auth cache from logging
    # this device straight back in. Explicit form login removes the marker.
    if not request.cookies.get(LOGGED_OUT):
        # Prefer the revocable session over a stale Basic header cached by an
        # older browser/client before migration or a server password change.
        if current_session(request):
            return username
        if credentials is not None:
            auth_attempt(request)
            valid = check_credentials(credentials.username, credentials.password)
            auth_attempt(request, valid)
            if valid:
                return username
    # Fetch requests from our cookie UI must reach the login-page redirect in JS,
    # rather than raising a second native/browser Basic-auth password dialog.
    headers = {} if request.headers.get('x-accufolio') == '1' and credentials is None else {
        'WWW-Authenticate': 'Basic realm="Accufolio"'}
    raise HTTPException(401, 'Please sign in', headers=headers)


def session_csrf(request: Request):
    if request.headers.get('x-accufolio') != '1':
        raise HTTPException(403, 'Missing request header')
    origin = request.headers.get('origin')
    expected_origin = public_origin() or str(request.base_url).rstrip('/')
    if origin and origin != expected_origin:
        raise HTTPException(403, 'Invalid request origin')


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=4096)
    remember: bool = True


def create_session(request, response, remember):
    token = secrets.token_urlsafe(32)
    expiry = int(time.time()) + (PERSIST_SECONDS if remember else SESSION_SECONDS)
    with sessions() as con:
        con.execute('DELETE FROM device_sessions WHERE expires_at<=? OR fingerprint<>?',
                    (int(time.time()), credential_fingerprint()))
        prior = request.cookies.get(COOKIE, '')
        if prior:
            con.execute('DELETE FROM device_sessions WHERE token_hash=?', (token_hash(prior),))
        con.execute('INSERT INTO device_sessions VALUES(?,?,?,?)',
                    (token_hash(token), credential_fingerprint(), expiry, int(remember)))
    secure = secure_cookie(request)
    response.set_cookie(COOKIE, token, max_age=PERSIST_SECONDS if remember else None,
                        secure=secure, httponly=True, samesite='strict', path='/')
    response.delete_cookie(LOGGED_OUT, secure=secure, httponly=True, samesite='strict', path='/')
    return expiry


@router.get('/login')
def login_page():
    return HTMLResponse((ROOT / 'apps/web/login.html').read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin'})


@router.post('/api/session', dependencies=[Depends(session_csrf)])
def login(payload: Login, request: Request):
    auth_attempt(request)
    valid = check_credentials(payload.username, payload.password)
    auth_attempt(request, valid)
    if not valid:
        # No Basic challenge here: the form should display one inline error.
        raise HTTPException(401, '用户名或密码不正确')
    response = JSONResponse({'authenticated': True, 'remembered': payload.remember})
    create_session(request, response, payload.remember)
    return response


@router.get('/api/session')
def session_status(request: Request, actor: str = Depends(authorize)):
    session = current_session(request)
    return {'authenticated': True, 'username': actor,
            'remembered': bool(session and session['remembered']),
            'expires_at': datetime.fromtimestamp(session['expires_at'], timezone.utc).isoformat() if session else None}


@router.delete('/api/session', dependencies=[Depends(session_csrf)])
def logout(request: Request):
    # Allow an expired/revoked session to be cleared too.
    token = request.cookies.get(COOKIE, '')
    if token:
        with sessions() as con:
            con.execute('DELETE FROM device_sessions WHERE token_hash=?', (token_hash(token),))
    response = JSONResponse({'authenticated': False})
    secure = secure_cookie(request)
    response.delete_cookie(COOKIE, secure=secure, httponly=True, samesite='strict', path='/')
    response.set_cookie(LOGGED_OUT, '1', max_age=PERSIST_SECONDS, secure=secure,
                        httponly=True, samesite='strict', path='/')
    return response
