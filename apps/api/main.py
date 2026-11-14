"""Accufolio single-user HTTP API."""
import hashlib
import os
import sqlite3
from contextlib import contextmanager, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from apps.api.auth import authorize, router as auth_router
from pydantic import BaseModel, Field
from packages.core.portfolio import summarize_trades
from packages.core.metrics import xirr, twr, max_drawdown
from packages.core.backtest import run as run_backtest
from packages.providers import get_quote
from packages.core.market_time import et_today

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'apps' / 'web'
@asynccontextmanager
async def lifespan(app):
    import threading
    from apps.api.maintenance import run_worker
    # Fail at startup if the deployed database has not been adapted to v2.
    with db():
        pass
    stop = threading.Event()
    interval = max(60, int(os.environ.get('ACCUFOLIO_MAINTENANCE_INTERVAL', '300')))
    thread = threading.Thread(target=run_worker, args=(stop, interval), daemon=True, name='accufolio-maintenance')
    thread.start()
    yield
    stop.set()
    # Do not hold shutdown indefinitely for a large SQLite backup; heartbeat
    # state still distinguishes an interrupted backup from a completed cycle.
    thread.join(timeout=1)

app = FastAPI(title='Accufolio', version='2.3.0', lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    if (response.status_code == 401 and request.method == 'GET'
            and request.url.path in {'/', '/assets', '/investments', '/records', '/discover', '/settings'}
            and 'text/html' in request.headers.get('accept', '')):
        next_page = request.url.path if request.url.path != '/' else '/assets'
        response = RedirectResponse('/login?next=' + next_page, status_code=303)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    if response.status_code in {303, 401}:
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

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
        conn.execute('PRAGMA foreign_keys=ON')
        from packages.core.schema_v2 import initialize
        initialize(conn)
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

class Trade(BaseModel):
    account_id: int = Field(default=1, gt=0)
    currency: Literal['USD'] = 'USD'
    fee: float = Field(default=0, ge=0, allow_inf_nan=False)
    strategy: Literal['dca', 'dip_add', 'rebalance', 'manual'] = 'manual'
    symbol: str = Field(min_length=1, max_length=15, pattern=r'^[A-Za-z0-9.\-]+$')
    trade_date: date
    shares: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    note: str = Field(default='', max_length=500)
    side: Literal['buy', 'sell'] = 'buy'

@app.get('/api/version')
def version(actor: str = Depends(authorize)):
    digest = hashlib.sha256()
    paths = sorted(path for path in WEB.rglob('*') if path.is_file() and path.suffix in {'.html', '.js', '.css', '.webmanifest'})
    paths += [ROOT / 'assets' / 'icon-192.png', ROOT / 'assets' / 'icon-512.png']
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).replace('\\', '/').encode())
        digest.update(path.read_bytes())
    return {'version': digest.hexdigest(), 'release': '2.3.0'}

@app.get('/api/trades')
def trades(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(row) for row in con.execute('''SELECT t.*,a.name AS account_name
            FROM trades t JOIN accounts a ON a.id=t.account_id
            WHERE t.deleted=0 ORDER BY t.trade_date DESC,t.id DESC''')]

@app.post('/api/trades', dependencies=[Depends(csrf)], status_code=201)
def add_trade(trade: Trade, actor: str = Depends(authorize), idempotency_key: Annotated[str | None, Header()] = None):
    return mutate_trade('add', trade, None, actor, idempotency_key)

@app.put('/api/trades/{trade_id}', dependencies=[Depends(csrf)])
def update_trade(trade_id: int, trade: Trade, actor: str = Depends(authorize), idempotency_key: Annotated[str | None, Header()] = None):
    return mutate_trade('update', trade, trade_id, actor, idempotency_key)

def mutate_trade(action, trade, trade_id, actor, key=None, dry_run=False):
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
            from apps.api.settlements import require_unlinked
            require_unlinked(con, trade_id=trade_id)
        if action == 'add':
            validate_trade_account(con, data)
            # Never reuse an ID after deletion: old audits must keep identifying the same trade.
            trade_id = con.execute('INSERT INTO trades(symbol,trade_date,shares,price,note,side,account_id,currency,fee,strategy) VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(data[k] for k in ('symbol','trade_date','shares','price','note','side','account_id','currency','fee','strategy'))).lastrowid
        elif action == 'update':
            validate_trade_account(con, data)
            con.execute('UPDATE trades SET symbol=?,trade_date=?,shares=?,price=?,note=?,side=?,account_id=?,currency=?,fee=?,strategy=? WHERE id=?', tuple(data[k] for k in ('symbol','trade_date','shares','price','note','side','account_id','currency','fee','strategy')) + (trade_id,))
        else:
            con.execute('UPDATE trades SET deleted=1 WHERE id=?', (trade_id,))
        validate_trade_history(con)
        after = dict(con.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone())
        if dry_run:
            con.rollback()
            return {'preview':{'action':action,'before':before,'after':after}}
        # Settlement previews bind to the account revision, including its trades.
        # Moving a trade invalidates previews for both the source and destination.
        from apps.api.accounts import _stamp
        affected_accounts = {after['account_id']}
        if before is not None:
            affected_accounts.add(before['account_id'])
        for account_id in sorted(affected_accounts):
            _stamp(con, account_id)
        audit_id = con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)', (actor, action+'_trade', json.dumps({'id':trade_id, 'before':before, 'after':after}))).lastrowid
        result = {'id':trade_id, 'audit_id':audit_id, 'ok':True}
        if key: con.execute('INSERT INTO requests VALUES(?,?,?)', (key,payload,json.dumps(result)))
        return result


def validate_trade_account(con, data):
    account = con.execute('SELECT * FROM accounts WHERE id=?', (data['account_id'],)).fetchone()
    if not account or account['archived'] or account['kind'] != 'broker':
        raise HTTPException(422, 'Trades require an active broker account')
    if account['currency'] != data['currency'] or data['currency'] != 'USD':
        raise HTTPException(422, 'Current securities quotes and trades require a USD broker account')
    if account['opened_on'] and data['trade_date'] < account['opened_on']:
        raise HTTPException(422, 'Trade date cannot precede the account opening date')


def validate_trade_history(con):
    """All active trades must be backed by prior holdings, including after edits and undo."""
    rows = con.execute('SELECT * FROM trades WHERE deleted=0 ORDER BY symbol, trade_date, id').fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault((row['account_id'], row['currency'], row['symbol']), []).append(row)
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
def summary(symbol: str, actor: str = Depends(authorize)):
    """One symbol, aggregated after calculating each account's own cost basis."""
    from packages.core.dashboard import summarize_account
    symbol = symbol.upper()
    today = et_today()
    with db() as con:
        con.execute('BEGIN')
        rows = [dict(row) for row in con.execute('SELECT * FROM trades WHERE symbol=? AND deleted=0 AND trade_date<=? ORDER BY trade_date,id', (symbol,today.isoformat()))]
        cash_entries = [dict(row) for row in con.execute('SELECT * FROM cash_entries ORDER BY entry_date,id')]
        account_names = {row['id']: row['name'] for row in con.execute('SELECT id,name FROM accounts')}
    quote = get_quote(symbol)
    result = summarize_account(rows, {symbol:quote.quote.__dict__ if quote.quote else None}, today,
                               cash_entries=cash_entries, dividend_symbol=symbol)
    totals = result['summary']
    shares = sum(h['shares'] for h in result['holdings'])
    cost = totals['cost']
    position = None if quote.quote is None else {
        'shares':shares, 'cost':cost, 'average_price':cost/shares if shares else None,
        'value':totals['value'], 'pnl':totals['unrealized_pnl'],
        'return_rate':totals['unrealized_pnl']/cost if cost else None}
    return {'scope':'single_symbol', 'currency':'USD', 'as_of':today.isoformat(), 'timezone':'America/New_York',
            'symbol': symbol, 'shares': shares, 'cost': cost, 'average_price': cost/shares if shares else None,
            'position': position, 'realized_pnl': totals['realized_pnl'], 'fees':totals['fees'],
            'cash_dividends': totals['cash_dividends'], 'total_return': totals['total_return'],
            'dividend_basis': 'net_cash_paid',
            'dividends_complete': totals['dividends_complete'], 'dividend_issues': result['dividend_issues'],
            'dividend_breakdown': result['dividend_breakdown'],
            'account_names': account_names,
            'xirr': totals['xirr'], 'xirr_reason':totals['xirr_reason'], 'quote': quote.quote.__dict__ if quote.quote else None,
            'degraded': quote.degraded, 'errors': quote.errors}

app.include_router(auth_router)
from apps.api.extended import router
app.include_router(router)
from apps.api.history import router as history_router
app.include_router(history_router)
from apps.api.dashboard import router as dashboard_router
app.include_router(dashboard_router)
from apps.api.allocation import router as allocation_router
app.include_router(allocation_router)
from apps.api.accounts import router as accounts_router
app.include_router(accounts_router)
from apps.api.settlements import router as settlements_router
app.include_router(settlements_router)
from apps.api.asset_history import router as asset_history_router
app.include_router(asset_history_router)
from apps.api.preferences import router as preferences_router
app.include_router(preferences_router)
from apps.api.fx import router as fx_router
app.include_router(fx_router)

PAGES = {'assets': '资产', 'investments': '投资', 'records': '记录', 'discover': '发现', 'settings': '设置'}

@app.get('/')
def index(actor: str = Depends(authorize)):
    return RedirectResponse('/assets', status_code=307)

@app.get('/assets')
@app.get('/investments')
@app.get('/records')
@app.get('/discover')
@app.get('/settings')
def page(request: Request, actor: str = Depends(authorize)):
    name = request.url.path.lstrip('/')
    shell = (WEB / 'index.html').read_text(encoding='utf-8-sig')
    body = (WEB / 'pages' / (name + '.html')).read_text(encoding='utf-8-sig')
    html = shell.replace('<!--PAGE-->', body).replace('{{page}}', name).replace('{{title}}', PAGES[name])
    for key in PAGES:
        html = html.replace('{{current-' + key + '}}', 'aria-current="page"' if key == name else '')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

@app.get('/api/ui/pages')
def page_fragments(actor: str = Depends(authorize)):
    return {name: (WEB / 'pages' / (name + '.html')).read_text(encoding='utf-8-sig') for name in PAGES}

@app.get('/pages/{filename}')
def page_script(filename: str):
    if filename not in {name + '.js' for name in PAGES}:
        raise HTTPException(404)
    return FileResponse(WEB / 'pages' / filename, media_type='application/javascript', headers={'Cache-Control': 'no-cache'})

@app.get('/{name}')
def assets(name: str):
    allowed = {'app.js': ('app.js', 'application/javascript'), 'style.css': ('style.css', 'text/css'),
               'income.js': ('income.js', 'application/javascript'),
               'charts.js': ('charts.js', 'application/javascript'), 'charts.css': ('charts.css', 'text/css'),
               'dashboard.css': ('dashboard.css', 'text/css'),
               'sw.js': ('sw.js', 'application/javascript'), 'manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
               'icon-192.png': ('../../assets/icon-192.png', 'image/png'), 'icon-512.png': ('../../assets/icon-512.png', 'image/png')}
    if name not in allowed: raise HTTPException(404)
    path, mime = allowed[name]
    return FileResponse(WEB / path, media_type=mime, headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})
