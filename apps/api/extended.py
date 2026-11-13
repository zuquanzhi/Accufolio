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

def scan_all():
    day = datetime.now(timezone.utc).date().isoformat()
    rows = watchlist(actor='scheduler')
    results = []
    for row in rows:
        symbol = row['symbol']
        result = get_quote(symbol)
        q = result.quote
        error = '; '.join(result.errors) if result.degraded else None
        message = None
        if error:
            message = '行情数据异常：'+error
            kind = 'source'
        elif q and abs(q.change_percent) >= row['threshold']:
            message = f'{symbol} 涨跌幅 {q.change_percent:+.2f}%，达到 {row["threshold"]:g}% 阈值'
            kind = 'move'
        with db() as con:
            con.execute('INSERT INTO scans(symbol,price,change_percent,error) VALUES(?,?,?,?)',(symbol,q.price if q else None,q.change_percent if q else None,error))
            if message:
                con.execute('INSERT OR IGNORE INTO alerts(symbol,message,dedupe) VALUES(?,?,?)',(symbol,message,f'{day}:{symbol}:{kind}'))
            con.execute("DELETE FROM scans WHERE ts < datetime('now','-90 days')")
        results.append({'symbol':symbol,'degraded':result.degraded})
    return results

@router.post('/scan', dependencies=[Depends(csrf)])
def scan(actor: str = Depends(authorize)):
    return scan_all()

@router.get('/alerts')
def alerts(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 200')]

@router.get('/daily')
def daily(actor: str = Depends(authorize)):
    with db() as con:
        return [dict(r) for r in con.execute('SELECT substr(ts,1,10) AS day, symbol, count(*) AS scans, sum(error IS NOT NULL) AS failures, min(price) AS low, max(price) AS high FROM scans GROUP BY day,symbol ORDER BY day DESC,symbol LIMIT 200')]

@router.get('/health')
def health(actor: str = Depends(authorize)):
    with db() as con:
        return {'release':'1.4.0','database':con.execute('PRAGMA quick_check').fetchone()[0], 'last_scan':con.execute('SELECT max(ts) FROM scans').fetchone()[0], 'timezone':'UTC'}

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
