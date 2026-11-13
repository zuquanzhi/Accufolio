"""Named accounts and an append-only cash journal; HTTP exposes read operations only."""
from datetime import date, datetime, timedelta, timezone
import json
import math
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from apps.api.auth import authorize

router = APIRouter()


def _db():
    from apps.api.main import db
    return db()


class Account(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['broker','bank','ewallet','provident','other']
    purpose: Literal['investing','savings','emergency','spending','housing_fund']
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    institution: str = Field(default='', max_length=100)
    opened_on: date | None = None
    archived: bool = False
    note: str = Field(default='', max_length=500)

    @field_validator('name')
    @classmethod
    def meaningful_name(cls, value):
        if not value.strip():
            raise ValueError('Account name cannot be blank')
        return value.strip()


class CashEntry(BaseModel):
    account_id: int = Field(gt=0, strict=True)
    entry_date: date
    kind: Literal['balance_open','deposit','withdraw','interest','dividend','fee','fx_in','fx_out','transfer_in','transfer_out']
    amount: float = Field(allow_inf_nan=False)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    fx_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    counter_account_id: int | None = Field(default=None, gt=0, strict=True)
    symbol: str | None = Field(default=None, pattern=r'^[A-Z0-9][A-Z0-9.\-]{0,14}$')
    note: str = Field(default='', max_length=500)

    @field_validator('amount', 'fx_rate', mode='before')
    @classmethod
    def numeric_not_boolean(cls, value):
        if isinstance(value, bool):raise ValueError('Money and exchange rates cannot be booleans')
        return value


def _check_request(expected_revision, key):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a non-negative integer')
    if not isinstance(key,str) or not key.strip() or len(key)>200:
        raise ValueError('A unique idempotency_key of at most 200 characters is required')


def _replay(con,key,identity):
    row=con.execute('SELECT payload,result FROM requests WHERE key=?',(key,)).fetchone()
    if row:
        if row['payload']!=identity:
            raise HTTPException(409,'Idempotency key already used with different data')
        return json.loads(row['result'])


def _account(con, account_id, revision=None, active=False):
    if type(account_id) is not int or not 0<account_id<2**63:raise ValueError('account_id must be a positive integer')
    row=con.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone()
    if row is None:raise HTTPException(404,'Account not found')
    if revision is not None and row['revision']!=revision:raise HTTPException(409,'Account changed; read its current revision')
    if active and row['archived']:raise ValueError('Archived accounts cannot receive entries')
    return dict(row)


def _stamp(con, account_id):
    con.execute('UPDATE accounts SET revision=revision+1,updated_at=? WHERE id=?',
                (datetime.now(timezone.utc).isoformat(),account_id))


def _persist(con, actor, action, details, key, identity, result):
    audit_id=con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',
                         (actor,action,json.dumps(details,ensure_ascii=False))).lastrowid
    result={**result,'audit_id':audit_id}
    con.execute('INSERT INTO requests VALUES(?,?,?)',(key,identity,json.dumps(result)))
    return result


def _rows(con, account_id, as_of=None):
    query='SELECT * FROM cash_entries WHERE account_id=?'
    args=[account_id]
    if as_of is not None:query+=' AND entry_date<=?';args.append(str(as_of))
    return [dict(r) for r in con.execute(query+' ORDER BY entry_date,id',args)]


def _cash_summary(con, row, as_of=None):
    entries=_rows(con,row['id'],as_of)
    return {**row,'balance':math.fsum(e['amount'] for e in entries),
            'as_of':max((e['entry_date'] for e in entries),default=None),'entry_count':len(entries),
            'archived':bool(row['archived'])}


@router.get('/api/accounts')
def list_accounts(actor: str=Depends(authorize)):
    with _db() as con:
        return [_cash_summary(con,dict(row)) for row in con.execute('SELECT * FROM accounts ORDER BY archived,id')]


@router.get('/api/accounts/{account_id}/cash-entries')
def list_cash_entries(account_id:int,actor: str=Depends(authorize)):
    with _db() as con:
        _account(con,account_id)
        return [{**row,'effective_date':row['entry_date']} for row in reversed(_rows(con,account_id))]


def save_account(account:Account,account_id:int|None,expected_revision:int,key:str,dry_run=True,actor='mcp'):
    _check_request(expected_revision,key)
    if account_id is not None and (type(account_id) is not int or not 0<account_id<2**63):
        raise ValueError('account_id must be a positive integer or null for a new account')
    data=account.model_dump(mode='json')
    identity=json.dumps(['set_account',account_id,data,expected_revision,actor],sort_keys=True)
    with _db() as con:
        con.execute('BEGIN IMMEDIATE')
        prior=_replay(con,key,identity)
        if prior:return prior
        before=_account(con,account_id,expected_revision) if account_id else None
        if before is None and expected_revision!=0:raise ValueError('New account expected_revision must be zero')
        if con.execute('SELECT 1 FROM accounts WHERE name=? AND id!=?',(account.name,account_id or 0)).fetchone():
            raise ValueError('Account name already exists')
        if before:
            has_cash=con.execute('SELECT 1 FROM cash_entries WHERE account_id=? LIMIT 1',(account_id,)).fetchone()
            has_trades=con.execute('SELECT 1 FROM trades WHERE account_id=? LIMIT 1',(account_id,)).fetchone()
            if data['currency']!=before['currency'] and (has_cash or has_trades):
                raise ValueError('An account with history cannot change currency; create a separate account')
            if has_trades and data['kind']!='broker':raise ValueError('Securities history requires a brokerage account')
            if data['opened_on']:
                first_dates=[r[0] for r in con.execute('SELECT MIN(entry_date) FROM cash_entries WHERE account_id=? UNION ALL SELECT MIN(trade_date) FROM trades WHERE account_id=?',(account_id,account_id)) if r[0]]
                if first_dates and data['opened_on']>min(first_dates):raise ValueError('Account opening date cannot be later than its recorded history')
            if account.archived:
                if abs(math.fsum(r['amount'] for r in _rows(con,account_id)))>1e-8:
                    raise ValueError('An account with a cash balance cannot be archived')
                open_positions=con.execute("SELECT symbol,SUM(CASE WHEN side='buy' THEN shares ELSE -shares END) AS quantity FROM trades WHERE account_id=? AND deleted=0 GROUP BY symbol HAVING ABS(quantity)>0.00000001",(account_id,)).fetchall()
                if open_positions:raise ValueError('An account with securities cannot be archived')
        if dry_run:return {'preview':data,'account_id':account_id,'expected_revision':expected_revision}
        columns=list(data);values=[int(data[k]) if k=='archived' else data[k] for k in columns]
        if before:
            con.execute('UPDATE accounts SET '+','.join(k+'=?' for k in columns)+' WHERE id=?',values+[account_id])
            _stamp(con,account_id)
        else:
            account_id=con.execute('INSERT INTO accounts('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values).lastrowid
        result=_account(con,account_id)
        return _persist(con,actor,'set_account',{'before':before,'after':result},key,identity,result)


def _validate_entry(con, entry:CashEntry, account, allow_linked=False):
    if entry.currency!=account['currency']:raise ValueError('Entry currency must match its account')
    # Cash dates are civil dates for worldwide accounts, rather than US sessions.
    if entry.entry_date>(datetime.now(timezone.utc)+timedelta(hours=14)).date():raise ValueError('Cash entry date cannot be in the future')
    if account['opened_on'] and entry.entry_date.isoformat()<account['opened_on']:raise ValueError('Cash entry predates account opening')
    if entry.kind in ('fx_in','fx_out','transfer_in','transfer_out') and not allow_linked:
        raise ValueError('Use transfer_cash to record both transfer legs atomically')
    if not allow_linked and (entry.counter_account_id is not None or entry.fx_rate is not None):
        raise ValueError('Counter-account and exchange-rate fields require a paired transfer')
    positive=('balance_open','deposit','interest','dividend','fx_in','transfer_in')
    if entry.kind in positive and (entry.amount<0 or entry.amount==0 and entry.kind!='balance_open'):
        raise ValueError('Incoming cash entries must be positive')
    if entry.kind not in positive and entry.amount>=0:raise ValueError('Outgoing cash entries must be negative')
    if entry.kind=='balance_open' and _rows(con,entry.account_id):raise ValueError('Opening balance must be the first cash entry')
    if entry.kind in ('fx_in','fx_out') and entry.fx_rate is None:raise ValueError('Currency exchanges require an explicit FX rate')
    rows=_rows(con,entry.account_id)+[{**entry.model_dump(mode='json'),'id':float('inf')}]
    running=0.0
    for row in sorted(rows,key=lambda r:(r['entry_date'],r['id'])):
        running+=row['amount']
        if not math.isfinite(running) or running < -1e-8:raise ValueError('Cash entry would create a negative historical balance')


def _insert_entry(con,entry,group=None,reverses=None):
    data=entry.model_dump(mode='json');columns=list(data)
    entry_id=con.execute('INSERT INTO cash_entries('+','.join(columns)+',transfer_group,reverses_entry_id) VALUES('+','.join('?' for _ in columns)+',?,?)',
                         [data[k] for k in columns]+[group,reverses]).lastrowid
    _stamp(con,entry.account_id)
    return dict(con.execute('SELECT * FROM cash_entries WHERE id=?',(entry_id,)).fetchone())


def add_cash_entry(entry:CashEntry,expected_revision:int,key:str,dry_run=True,actor='mcp'):
    _check_request(expected_revision,key)
    data=entry.model_dump(mode='json');identity=json.dumps(['cash_entry',data,expected_revision,actor],sort_keys=True)
    with _db() as con:
        con.execute('BEGIN IMMEDIATE');prior=_replay(con,key,identity)
        if prior:return prior
        account=_account(con,entry.account_id,expected_revision,active=True)
        _validate_entry(con,entry,account)
        if dry_run:return {'preview':data,'expected_revision':expected_revision,'balance_after':_cash_summary(con,account)['balance']+entry.amount}
        created=_insert_entry(con,entry);result={'entry':created,'account':_cash_summary(con,_account(con,entry.account_id))}
        return _persist(con,actor,'add_cash_entry',{'entry':created},key,identity,result)


def transfer_cash(source_id:int,destination_id:int,source_amount:float,destination_amount:float,entry_date:str,
                  source_revision:int,destination_revision:int,key:str,note='',dry_run=True,actor='mcp'):
    _check_request(source_revision,key);_check_request(destination_revision,key)
    if source_id==destination_id:raise ValueError('Transfers require two different accounts')
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0 for v in (source_amount,destination_amount)):
        raise ValueError('Transfer amounts must be finite positive numbers')
    identity=json.dumps(['cash_transfer',source_id,destination_id,source_amount,destination_amount,entry_date,source_revision,destination_revision,note,actor],sort_keys=True)
    with _db() as con:
        con.execute('BEGIN IMMEDIATE');prior=_replay(con,key,identity)
        if prior:return prior
        source=_account(con,source_id,source_revision,active=True);destination=_account(con,destination_id,destination_revision,active=True)
        exchange=source['currency']!=destination['currency']
        if not exchange and abs(source_amount-destination_amount)>1e-8:raise ValueError('Same-currency transfer amounts must match; record fees separately')
        rate=destination_amount/source_amount if exchange else None
        outgoing=CashEntry(account_id=source_id,entry_date=entry_date,kind='fx_out' if exchange else 'transfer_out',amount=-source_amount,currency=source['currency'],fx_rate=rate,counter_account_id=destination_id,note=note)
        incoming=CashEntry(account_id=destination_id,entry_date=entry_date,kind='fx_in' if exchange else 'transfer_in',amount=destination_amount,currency=destination['currency'],fx_rate=1/rate if exchange else None,counter_account_id=source_id,note=note)
        for entry,account in [(outgoing,source),(incoming,destination)]:_validate_entry(con,entry,account,allow_linked=True)
        if dry_run:return {'preview':[outgoing.model_dump(mode='json'),incoming.model_dump(mode='json')]}
        group=str(uuid.uuid4());entries=[_insert_entry(con,e,group=group) for e in [outgoing,incoming]]
        result={'entries':entries,'accounts':[_cash_summary(con,_account(con,i)) for i in [source_id,destination_id]]}
        return _persist(con,actor,'transfer_cash',{'entries':entries},key,identity,result)


def reverse_cash_entry(entry_id:int,entry_date:str,expected_revision:int,key:str,note='',dry_run=True,actor='mcp'):
    _check_request(expected_revision,key)
    identity=json.dumps(['reverse_cash_entry',entry_id,entry_date,expected_revision,note,actor],sort_keys=True)
    with _db() as con:
        con.execute('BEGIN IMMEDIATE');prior=_replay(con,key,identity)
        if prior:return prior
        row=con.execute('SELECT * FROM cash_entries WHERE id=?',(entry_id,)).fetchone()
        if row is None:raise HTTPException(404,'Cash entry not found')
        before=dict(row)
        if before['transfer_group']:raise ValueError('Use a new reverse transfer to correct paired transfer entries')
        if before['reverses_entry_id'] or con.execute('SELECT 1 FROM cash_entries WHERE reverses_entry_id=?',(entry_id,)).fetchone():
            raise ValueError('Entry is already a reversal or has been reversed')
        if entry_date<before['entry_date']:raise ValueError('Reversal cannot predate the original entry')
        account=_account(con,before['account_id'],expected_revision,active=True)
        if before['amount']==0:raise ValueError('Zero opening balance does not need reversal')
        entry=CashEntry(account_id=account['id'],entry_date=entry_date,kind='withdraw' if before['amount']>0 else 'deposit',amount=-before['amount'],currency=before['currency'],symbol=before['symbol'],note=note or f'冲销现金流水 #{entry_id}')
        _validate_entry(con,entry,account)
        if dry_run:return {'preview':entry.model_dump(mode='json'),'reverses_entry_id':entry_id}
        created=_insert_entry(con,entry,reverses=entry_id)
        result={'entry':created,'account':_cash_summary(con,_account(con,account['id']))}
        return _persist(con,actor,'reverse_cash_entry',{'original':before,'reversal':created},key,identity,result)


@router.get('/api/assets')
def assets(actor: str=Depends(authorize)):
    from apps.api.fx import get_fx
    from packages.core.dashboard import summarize_account
    from packages.core.market_time import et_today
    from packages.providers import get_quote
    from concurrent.futures import ThreadPoolExecutor
    today=et_today()
    with _db() as con:
        # Read balances and securities from one ledger snapshot, then release it
        # before any provider I/O so data fetching never blocks account writers.
        con.execute('BEGIN')
        accounts=[_cash_summary(con,dict(row)) for row in con.execute('SELECT * FROM accounts WHERE archived=0 ORDER BY id')]
        trades=[dict(row) for row in con.execute('SELECT * FROM trades WHERE deleted=0 AND trade_date<=?',(today.isoformat(),))]
    grouped={a['id']:[t for t in trades if t['account_id']==a['id']] for a in accounts}
    symbols=sorted({h['symbol'] for rows in grouped.values() for h in summarize_account(rows,{},today)['holdings']})
    quotes={};sources=[];warnings=[]
    def quote(symbol):
        try:
            result=get_quote(symbol)
            return symbol,result.quote.__dict__ if result.quote else None,result.degraded
        except Exception:return symbol,None,True
    if symbols:
        with ThreadPoolExecutor(max_workers=min(4,len(symbols))) as pool:
            for symbol,value,degraded in pool.map(quote,symbols):
                quotes[symbol]=value
                sources.append({'symbol':symbol,'source':value.get('source') if value else None,
                                'quote_timestamp':value.get('timestamp') if value else None,
                                'fetched_at':value.get('fetched_at') if value else None,
                                'degraded':bool(degraded),'available':value is not None})
                if value is None:warnings.append(f'{symbol}：最近行情暂不可用，相关账户估值与净资产暂不汇总。')
                elif degraded:warnings.append(f'{symbol}：当前使用备用行情来源，请核对行情时间。')
        warnings.append('证券按最近已知行情估值，来源可能延迟；行情时间与现金余额日期分别列示。')
    identity_fx={'base':'USD','rates':{'USD':1},'as_of':None,'source':'identity','available':True,'stale':False}
    try:
        fx=get_fx() if any(a['currency']!='USD' for a in accounts) else identity_fx
    except Exception:
        fx={**identity_fx,'source':'ECB','available':False,'errors':['参考汇率暂不可用。']}
    if fx.get('base')!='USD':fx={**identity_fx,'available':False}
    missing_fx=[];missing_quotes=[]
    for account in accounts:
        summary=summarize_account(grouped[account['id']],quotes,today)
        security=summary['summary']['value']
        missing_quotes.extend(summary['quote_missing_symbols'])
        rate=1 if account['currency']=='USD' else fx.get('rates',{}).get(account['currency'])
        valid_rate=isinstance(rate,(int,float)) and not isinstance(rate,bool) and math.isfinite(rate) and rate>0
        cash=0.0 if account['balance']==0 else account['balance']/rate if valid_rate else None
        if cash is None:missing_fx.append(account['currency'])
        timestamps=[h['quote_timestamp'] for h in summary['holdings'] if isinstance(h.get('quote_timestamp'),(int,float)) and math.isfinite(h['quote_timestamp']) and h['quote_timestamp']>0]
        account.update(cash_value_usd=cash,securities_value_usd=security,
                       value_usd=cash+security if cash is not None and security is not None else None,
                       fx_rate_to_usd=1/rate if valid_rate else None,
                       fx_as_of=fx.get('as_of') if account['currency']!='USD' else None,
                       quote_timestamp_min=min(timestamps) if timestamps else None,
                       quote_timestamp_max=max(timestamps) if timestamps else None)
    cash_value=None if missing_fx else math.fsum(a['cash_value_usd'] for a in accounts)
    security_value=None if missing_quotes else math.fsum(a['securities_value_usd'] for a in accounts)
    complete=cash_value is not None and security_value is not None
    if fx.get('stale'):warnings.append('当前账户折算使用过期参考汇率，请留意汇率日期。')
    return {'as_of':today.isoformat(),'base':'USD','net_assets_usd':cash_value+security_value if complete else None,
            'securities_value_usd':security_value,'cash_value_usd':cash_value,'complete':complete,
            'missing_fx':sorted(set(missing_fx)),'missing_quotes':sorted(set(missing_quotes)),
            'accounts':accounts,'fx':fx,'quote_sources':sources,'warnings':list(dict.fromkeys(warnings))}
