"""Export, backup health and audited undo routes."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from apps.api.main import authorize, csrf, db, ROOT

router = APIRouter(prefix='/api')


@router.get('/health')
def health(actor: str = Depends(authorize)):
    from apps.api.maintenance import worker_status, backup_status
    worker = worker_status()
    backup = backup_status()
    with db() as con:
        return {'release':'2.2.0', 'database':con.execute('PRAGMA quick_check').fetchone()[0],
                'timezone':'UTC', 'worker':worker, 'backup':backup}

@router.get('/export')
def export(actor: str = Depends(authorize)):
    # One read transaction: account balances and their journal cannot diverge
    # if an Agent records a transfer while the export is being assembled.
    with db() as con:
        con.execute('BEGIN')
        data = {'format':'accufolio-v2', 'exported_at':datetime.now(timezone.utc).isoformat()}
        for table in ('accounts','cash_entries','trades','fx_rates','settings','allocation_targets'):
            data[table] = [dict(row) for row in con.execute('SELECT * FROM ' + table)]
    return Response(json.dumps(data,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="accufolio.json"','Cache-Control':'no-store'})

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
        if not current or dict(current) != data['after']: raise HTTPException(409,'Trade changed since this action; undo newer changes first')
        before=data['before']
        if before is None:
            con.execute('UPDATE trades SET deleted=1 WHERE id=?',(data['id'],))
        else:
            from apps.api.main import validate_trade_account
            if not before['deleted']:
                validate_trade_account(con, before)
            con.execute('UPDATE trades SET symbol=?,trade_date=?,shares=?,price=?,note=?,deleted=?,side=?,account_id=?,currency=?,fee=?,strategy=? WHERE id=?',tuple(before[k] for k in ('symbol','trade_date','shares','price','note','deleted','side','account_id','currency','fee','strategy'))+(data['id'],))
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
