"""Explicit securities/account target weights, never manually entered aggregate money."""
from datetime import datetime, timezone
import json
import math
import re

from fastapi import APIRouter, Depends, HTTPException
from apps.api.auth import authorize

router = APIRouter()


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS allocation_targets(
        scope TEXT NOT NULL CHECK(scope IN ('securities','net_worth')),
        key TEXT NOT NULL, weight REAL NOT NULL CHECK(weight>=0 AND weight<=100),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(scope,key))''')
    con.execute('''CREATE TABLE IF NOT EXISTS allocation_state(
        id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,updated_at TEXT NOT NULL)''')


def read_plan(con):
    schema(con)
    state = con.execute('SELECT * FROM allocation_state WHERE id=1').fetchone()
    result = {'securities': {}, 'net_worth': {}, 'revision':state['revision'] if state else 0,
              'updated_at':state['updated_at'] if state else None}
    for row in con.execute('SELECT scope,key,weight FROM allocation_targets ORDER BY scope,key'):
        result[row['scope']][row['key']] = row['weight']
    return result


@router.get('/api/allocation')
def allocation(actor: str = Depends(authorize)):
    from apps.api.main import db
    with db() as con:
        return read_plan(con)


def save_targets(scope, targets, expected_revision, key, dry_run=True, actor='mcp'):
    from apps.api.main import db
    if scope not in ('securities','net_worth') or not isinstance(targets,dict):
        raise ValueError('Choose securities or net_worth and provide a weights object')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a non-negative integer')
    if not isinstance(key,str) or not key.strip() or len(key)>200:
        raise ValueError('A nonblank idempotency_key of at most 200 characters is required')
    weights = {}
    for raw,value in targets.items():
        if not isinstance(raw,str):
            raise ValueError('Target key must be a string')
        name = raw.strip().upper() if scope=='securities' else raw.strip()
        pattern = r'[A-Z0-9][A-Z0-9.\-]{0,14}' if scope=='securities' else r'account:[1-9][0-9]*'
        if not re.fullmatch(pattern,name) or name in weights:
            raise ValueError('Use unique security symbols or explicit account:<id> keys')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
            raise ValueError('Weights must be finite percentages from 0 to 100')
        weights[name] = value
    if weights and abs(sum(weights.values())-100)>1e-6:
        raise ValueError('Weights within a scope must sum to 100')
    identity=json.dumps(['allocation_targets',scope,weights,expected_revision,actor],sort_keys=True)
    with db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        previous=con.execute('SELECT payload,result FROM requests WHERE key=?',(key,)).fetchone()
        if previous:
            if previous['payload']!=identity:
                raise HTTPException(409,'Idempotency key already used with different data')
            return json.loads(previous['result'])
        before=read_plan(con)
        if before['revision']!=expected_revision:
            raise HTTPException(409,'Targets changed; read the latest revision')
        if scope=='net_worth':
            for name in weights:
                account=con.execute('SELECT archived FROM accounts WHERE id=?',(int(name.split(':')[1]),)).fetchone()
                if account is None or account['archived']:
                    raise ValueError('Net-worth targets require active account IDs')
        if dry_run:
            return {'preview':{'scope':scope,'targets':weights},'expected_revision':expected_revision}
        stamp=datetime.now(timezone.utc).isoformat()
        con.execute('DELETE FROM allocation_targets WHERE scope=?',(scope,))
        con.executemany('INSERT INTO allocation_targets VALUES(?,?,?,?)',[(scope,name,value,stamp) for name,value in weights.items()])
        con.execute('''INSERT INTO allocation_state VALUES(1,?,?) ON CONFLICT(id) DO UPDATE
                    SET revision=excluded.revision,updated_at=excluded.updated_at''',(expected_revision+1,stamp))
        result=read_plan(con)
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',
                    (actor,'allocation_targets',json.dumps({'before':before,'after':result})))
        con.execute('INSERT INTO requests VALUES(?,?,?)',(key,identity,json.dumps(result)))
        return result
