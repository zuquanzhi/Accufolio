"""Accufolio single-user HTTP API."""
import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from packages.core.portfolio import summarize_trades
from packages.core.metrics import xirr, twr, max_drawdown
from packages.core.backtest import run as run_backtest
from packages.providers import get_quote

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'apps' / 'web'
security = HTTPBasic()
@asynccontextmanager
async def lifespan(app):
    import threading
    import logging
    from apps.api.extended import scan_all, backup
    stop = threading.Event()
    interval = max(60, int(os.environ.get('ACCUFOLIO_SCAN_INTERVAL', '300')))
    def worker():
        last_backup = None
        while not stop.is_set():
            try:
                today = date.today().isoformat()
                if last_backup != today:
                    base = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT/'data'/'accufolio.db'))).parent
                    backup(base/'backups'/f'accufolio-{today}.db')
                    last_backup = today
                scan_all()
            except Exception:
                logging.exception('Accufolio background scan failed')
            stop.wait(interval)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    yield
    stop.set()

app = FastAPI(title='Accufolio', version='1.3.0', lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

def authorize(credentials: Annotated[HTTPBasicCredentials, Depends(security)]):
    username = os.environ.get('ACCUFOLIO_USER', 'admin')
    password = os.environ.get('ACCUFOLIO_PASSWORD', '')
    if not password:
        raise HTTPException(503, 'Set ACCUFOLIO_PASSWORD before starting Accufolio')
    if not (hmac.compare_digest(credentials.username.encode(), username.encode()) and hmac.compare_digest(credentials.password.encode(), password.encode())):
        raise HTTPException(401, 'Invalid credentials', headers={'WWW-Authenticate': 'Basic'})
    return credentials.username

def csrf(x_accufolio: Annotated[str | None, Header()] = None):
    if x_accufolio != '1':
        raise HTTPException(403, 'Missing request header')

@contextmanager
def db():
    path = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT / 'data' / 'accufolio.db')))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=10000')
    try:
        conn.executescript('''CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);''')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(trades)')}
        if 'deleted' not in columns:
            conn.execute('ALTER TABLE trades ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        if 'side' not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN side TEXT NOT NULL DEFAULT 'buy'")
        conn.executescript('''CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watchlist(symbol TEXT PRIMARY KEY, threshold REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, symbol TEXT NOT NULL, price REAL, change_percent REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, symbol TEXT NOT NULL, message TEXT NOT NULL, dedupe TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS reversals(audit_id INTEGER PRIMARY KEY);''')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

class Trade(BaseModel):
    symbol: str = Field(min_length=1, max_length=15, pattern=r'^[A-Za-z0-9.\-]+$')
    trade_date: date
    shares: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    note: str = Field(default='', max_length=500)
    side: Literal['buy', 'sell'] = 'buy'

@app.get('/api/version')
def version(actor: str = Depends(authorize)):
    digest = hashlib.sha256()
    for path in (WEB / 'index.html', WEB / 'style.css', WEB / 'app.js', ROOT / 'assets' / 'icon-192.png', ROOT / 'assets' / 'icon-512.png'):
        digest.update(path.read_bytes())
    return {'version': digest.hexdigest(), 'release': '1.3.0'}

@app.get('/api/trades')
def trades(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(row) for row in con.execute('SELECT * FROM trades WHERE deleted=0 ORDER BY trade_date DESC, id DESC')]

@app.post('/api/trades', dependencies=[Depends(csrf)], status_code=201)
def add_trade(trade: Trade, actor: str = Depends(authorize), idempotency_key: Annotated[str | None, Header()] = None):
    return mutate_trade('add', trade, None, actor, idempotency_key)

@app.put('/api/trades/{trade_id}', dependencies=[Depends(csrf)])
def update_trade(trade_id: int, trade: Trade, actor: str = Depends(authorize), idempotency_key: Annotated[str | None, Header()] = None):
    return mutate_trade('update', trade, trade_id, actor, idempotency_key)

def mutate_trade(action, trade, trade_id, actor, key=None):
    import json
    data = trade.model_dump(mode='json') if trade else None
    if data: data['symbol'] = data['symbol'].upper()
    payload = json.dumps([action, trade_id, data, actor], sort_keys=True)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        if key:
            if len(key) > 200: raise HTTPException(422, 'Idempotency key too long')
            prior = con.execute('SELECT payload,result FROM requests WHERE key=?', (key,)).fetchone()
            if prior:
                if prior['payload'] != payload: raise HTTPException(409, 'Idempotency key already used with different data')
                return json.loads(prior['result'])
        before = None
        if action != 'add':
            row = con.execute('SELECT * FROM trades WHERE id=? AND deleted=0', (trade_id,)).fetchone()
            if row is None: raise HTTPException(404, 'Trade not found')
            before = dict(row)
        if action == 'add':
            # Never reuse an ID after deletion: old audits must keep identifying the same trade.
            trade_id = con.execute('INSERT INTO trades(symbol,trade_date,shares,price,note,side) VALUES(?,?,?,?,?,?)', tuple(data[k] for k in ('symbol','trade_date','shares','price','note','side'))).lastrowid
        elif action == 'update':
            con.execute('UPDATE trades SET symbol=?,trade_date=?,shares=?,price=?,note=?,side=? WHERE id=?', tuple(data[k] for k in ('symbol','trade_date','shares','price','note','side')) + (trade_id,))
        else:
            con.execute('UPDATE trades SET deleted=1 WHERE id=?', (trade_id,))
        validate_trade_history(con)
        after = dict(con.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone())
        audit_id = con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)', (actor, action+'_trade', json.dumps({'id':trade_id, 'before':before, 'after':after}))).lastrowid
        result = {'id':trade_id, 'audit_id':audit_id, 'ok':True}
        if key: con.execute('INSERT INTO requests VALUES(?,?,?)', (key,payload,json.dumps(result)))
        return result


def validate_trade_history(con):
    """All active trades must be backed by prior holdings, including after edits and undo."""
    rows = con.execute('SELECT * FROM trades WHERE deleted=0 ORDER BY symbol, trade_date, id').fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['symbol'], []).append(row)
    try:
        for history in grouped.values():
            summarize_trades(history, 0)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.get('/api/audit')
def audit(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(row) for row in con.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]

class Bar(BaseModel):
    day: date
    close: float = Field(gt=0, allow_inf_nan=False)

class BacktestRequest(BaseModel):
    bars: list[Bar] = Field(min_length=1, max_length=10000)
    monthly: float = Field(gt=0, allow_inf_nan=False)

@app.post('/api/backtest', dependencies=[Depends(csrf)])
def backtest(request: BacktestRequest, actor: str = Depends(authorize)):
    from dataclasses import asdict
    bars = [(b.day, b.close) for b in request.bars]
    try:
        strategies = {strategy: asdict(run_backtest(bars, request.monthly, strategy)) for strategy in ('fixed','quarterly','dip','blend')}
        drawdown, drawdown_date = max_drawdown(bars)
        return {'strategies': strategies, 'market': {'twr': twr(bars[0][1], bars[-1][1]), 'max_drawdown': drawdown, 'drawdown_date': drawdown_date}}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.delete('/api/trades/{trade_id}', dependencies=[Depends(csrf)])
def delete_trade(trade_id: int, actor: str = Depends(authorize), idempotency_key: Annotated[str | None, Header()] = None):
    return mutate_trade('delete', None, trade_id, actor, idempotency_key)

@app.get('/api/summary')
def summary(symbol: str = 'QQQM', actor: str = Depends(authorize)):
    symbol = symbol.upper()
    with db() as con:
        rows = con.execute('SELECT shares,price,trade_date,side FROM trades WHERE symbol=? AND deleted=0 ORDER BY trade_date,id', (symbol,)).fetchall()
    quote = get_quote(symbol)
    portfolio, realized = summarize_trades(rows, quote.quote.price if quote.quote else 0)
    cost, shares = portfolio.cost, portfolio.shares
    position = None if quote.quote is None else portfolio.__dict__
    annualized = None
    xirr_reason = '行情不可用' if position is None else '样本不足或没有持仓'
    if position and rows:
        flows = [(date.fromisoformat(row['trade_date']), (-1 if row['side'] == 'buy' else 1) * row['shares'] * row['price']) for row in rows]
        if any(day > date.today() for day, _ in flows):
            xirr_reason = '存在未来交易日期'
        else:
            try:
                annualized = xirr(flows + [(date.today(), position['value'])])
                if annualized is not None: xirr_reason = None
            except ValueError:
                xirr_reason = '无法在支持的范围内计算'
    return {'symbol': symbol, 'shares': shares, 'cost': cost, 'average_price': cost/shares if shares else None,
            'position': position, 'realized_pnl': realized, 'xirr': annualized, 'xirr_reason': xirr_reason, 'quote': quote.quote.__dict__ if quote.quote else None,
            'degraded': quote.degraded, 'errors': quote.errors}

from apps.api.extended import router
app.include_router(router)

@app.get('/')
def index(actor: str = Depends(authorize)):
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-store'})

@app.get('/{name}')
def assets(name: str):
    allowed = {'app.js': ('app.js', 'application/javascript'), 'style.css': ('style.css', 'text/css'),
               'sw.js': ('sw.js', 'application/javascript'), 'manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
               'icon-192.png': ('../../assets/icon-192.png', 'image/png'), 'icon-512.png': ('../../assets/icon-512.png', 'image/png')}
    if name not in allowed: raise HTTPException(404)
    path, mime = allowed[name]
    return FileResponse(WEB / path, media_type=mime, headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})
