"""Monitoring, export, health and audited undo routes."""
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from apps.api.main import authorize, csrf, db, ROOT
from packages.providers import get_quote
from packages.core import rules
from packages import notifications
from apps.api.signals import evaluate_calm

router = APIRouter(prefix='/api')

class Watch(BaseModel):
    symbol: str = Field(min_length=1, max_length=15, pattern=r'^[A-Za-z0-9.\-]+$')
    threshold: float = Field(default=2, gt=0, le=100, allow_inf_nan=False)

@router.get('/watchlist')
def watchlist(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM watchlist ORDER BY symbol')]

@router.put('/watchlist', dependencies=[Depends(csrf)])
def set_watchlist(item: Watch, actor: str = Depends(authorize)):
    with db() as con:
        symbol = item.symbol.upper()
        con.execute('INSERT INTO watchlist VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET threshold=excluded.threshold', (symbol,item.threshold))
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',(actor,'watchlist',json.dumps({'symbol':symbol,'threshold':item.threshold})))
    return {'ok': True}

@router.delete('/watchlist/{symbol}', dependencies=[Depends(csrf)])
def remove_watch(symbol: str, actor: str = Depends(authorize)):
    with db() as con:
        con.execute('DELETE FROM watchlist WHERE symbol=?',(symbol.upper(),))
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',(actor,'remove_watch',json.dumps({'symbol':symbol.upper()})))
    return {'ok':True}

def monitoring_schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS movement_high_water(
        day TEXT NOT NULL,symbol TEXT NOT NULL,direction INTEGER NOT NULL,level INTEGER NOT NULL,
        PRIMARY KEY(day,symbol,direction))''')
    con.execute('''CREATE TABLE IF NOT EXISTS monitoring_state(
        symbol TEXT PRIMARY KEY,updated_at TEXT NOT NULL,movement_level INTEGER NOT NULL,calm TEXT NOT NULL)''')
    notifications.ensure_schema(con)


def record_alert(con, symbol, message, dedupe, kind, level=None, as_of=None):
    cursor = con.execute('INSERT OR IGNORE INTO alerts(symbol,message,dedupe) VALUES(?,?,?)',
                         (symbol, message, dedupe))
    if cursor.rowcount:
        notifications.enqueue(con, cursor.lastrowid, symbol, message, kind, level, as_of)


def scan_all():
    day = datetime.now(timezone.utc).date().isoformat()
    rows = watchlist(actor='scheduler')
    results = []
    for row in rows:
        symbol = row['symbol']
        result = get_quote(symbol)
        q = result.quote
        error = '; '.join(result.errors) if result.degraded else None
        level = rules.movement_level(q.change_percent, row['threshold']) if q and not result.degraded else 0
        calm = evaluate_calm(symbol)
        with db() as con:
            monitoring_schema(con)
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO scans(symbol,price,change_percent,error) VALUES(?,?,?,?)',(symbol,q.price if q else None,q.change_percent if q else None,error))
            if error:
                record_alert(con, symbol, '行情数据异常：' + error, f'{day}:{symbol}:source', 'source', as_of=day)
            if level:
                direction = 1 if q.change_percent >= 0 else -1
                previous = con.execute('SELECT level FROM movement_high_water WHERE day=? AND symbol=? AND direction=?',
                                       (day, symbol, direction)).fetchone()
                if previous is None or level > previous['level']:
                    message = f'L{level} · {symbol} 涨跌幅 {q.change_percent:+.2f}%，达到 {row["threshold"] * level:g}% 阈值'
                    record_alert(con, symbol, message, f'{day}:{symbol}:move:{direction}:L{level}', 'move', level, day)
                    con.execute('''INSERT INTO movement_high_water VALUES(?,?,?,?)
                        ON CONFLICT(day,symbol,direction) DO UPDATE SET level=excluded.level''', (day, symbol, direction, level))
            if calm['status'] == 'triggered':
                as_of = calm['as_of']
                message = f'退潮信号 · {symbol}：截至 {as_of}，价格距 20 日高点至少 8%，VIX 自同期高点回落至少 25%'
                record_alert(con, symbol, message, f'{as_of}:{symbol}:calm', 'calm', as_of=as_of)
            con.execute('''INSERT INTO monitoring_state VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                updated_at=excluded.updated_at,movement_level=excluded.movement_level,calm=excluded.calm''',
                (symbol, datetime.now(timezone.utc).isoformat(), level, json.dumps(calm, ensure_ascii=False)))
            con.execute("DELETE FROM scans WHERE ts < datetime('now','-90 days')")
            con.execute("DELETE FROM movement_high_water WHERE day < date('now','-90 days')")
        results.append({'symbol':symbol,'degraded':result.degraded,'movement_level':level,'calm':calm})
    # Existing due notifications are retried even when the watchlist is empty.
    notifications.deliver_pending(db)
    return results

@router.post('/scan', dependencies=[Depends(csrf)])
def scan(actor: str = Depends(authorize)):
    return scan_all()

@router.get('/alerts')
def alerts(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 200')]

@router.get('/signals')
def signals(actor: str = Depends(authorize)):
    with db() as con:
        monitoring_schema(con)
        return [{**dict(row), 'calm': json.loads(row['calm'])} for row in con.execute(
            'SELECT s.* FROM monitoring_state s JOIN watchlist w ON w.symbol=s.symbol ORDER BY s.symbol')]

@router.get('/notifications/status')
def notification_status(actor: str = Depends(authorize)):
    return notifications.delivery_status(db)

@router.post('/notifications/retry', dependencies=[Depends(csrf)])
def retry_notifications(actor: str = Depends(authorize)):
    count = notifications.retry_failed(db)
    notifications.deliver_pending(db)
    return {'requeued': count, **notifications.delivery_status(db)}

@router.get('/daily')
def daily(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT substr(ts,1,10) AS day, symbol, count(*) AS scans, sum(error IS NOT NULL) AS failures, min(price) AS low, max(price) AS high FROM scans GROUP BY day,symbol ORDER BY day DESC,symbol LIMIT 200')]

@router.get('/health')
def health(actor: str = Depends(authorize)):
    delivery = notifications.delivery_status(db)
    with db() as con:
        return {'release':'1.5.0','database':con.execute('PRAGMA quick_check').fetchone()[0], 'last_scan':con.execute('SELECT max(ts) FROM scans').fetchone()[0], 'timezone':'UTC', 'notifications': delivery}

@router.get('/export')
def export(actor: str = Depends(authorize)):
    from apps.api.main import trades
    return Response(json.dumps({'format':'accufolio-trades-v1','trades':trades(actor=actor)},ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="accufolio-trades.json"','Cache-Control':'no-store'})

@router.post('/audit/{audit_id}/undo', dependencies=[Depends(csrf)])
def undo(audit_id: int, actor: str = Depends(authorize)):
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        audit = con.execute('SELECT * FROM audit WHERE id=?',(audit_id,)).fetchone()
        if not audit: raise HTTPException(404,'Audit not found')
        if con.execute('SELECT 1 FROM reversals WHERE audit_id=?',(audit_id,)).fetchone(): raise HTTPException(409,'Already undone')
        data = json.loads(audit['details'])
        if audit['action'] not in ('add_trade','update_trade','delete_trade') or 'after' not in data: raise HTTPException(422,'This audit cannot be undone')
        current = con.execute('SELECT * FROM trades WHERE id=?',(data['id'],)).fetchone()
        if 'side' not in data['after']: data['after']['side'] = 'buy'
        if not current or dict(current) != data['after']: raise HTTPException(409,'Trade changed since this action; undo newer changes first')
        before=data['before']
        if before is None:
            con.execute('UPDATE trades SET deleted=1 WHERE id=?',(data['id'],))
        else:
            before.setdefault('side', 'buy')
            con.execute('UPDATE trades SET symbol=?,trade_date=?,shares=?,price=?,note=?,deleted=?,side=? WHERE id=?',tuple(before[k] for k in ('symbol','trade_date','shares','price','note','deleted','side'))+(data['id'],))
        from apps.api.main import validate_trade_history
        validate_trade_history(con)
        con.execute('INSERT INTO reversals VALUES(?)',(audit_id,))
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',(actor,'undo',json.dumps({'audit_id':audit_id})))
    return {'ok':True}

def backup(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        target = sqlite3.connect(destination)
        try: con.backup(target)
        finally: target.close()

@router.post('/backup', dependencies=[Depends(csrf)])
def create_backup(actor: str = Depends(authorize)):
    import os
    base = Path(os.environ.get('ACCUFOLIO_DB', str(ROOT/'data'/'accufolio.db'))).parent
    name = 'accufolio-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.db'
    backup(base/'backups'/name)
    return {'ok':True,'filename':name}
