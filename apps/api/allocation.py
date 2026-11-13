"""Read-only allocation view; changes are audited local MCP operations."""
from datetime import date, datetime, timezone
import json
import math
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from apps.api.main import authorize, db

router = APIRouter()


class AllocationPlan(BaseModel):
    reserve_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reserve_as_of: date | None = None
    investment_targets: dict[str, float] = Field(default_factory=dict)
    total_targets: dict[str, float] = Field(default_factory=dict)
    note: str = Field(default='', max_length=500)

    @field_validator('investment_targets', 'total_targets')
    @classmethod
    def targets(cls, value, info):
        result = {}
        for symbol, weight in value.items():
            key = symbol.strip().upper()
            if key != '__RESERVE__' and not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,14}', key):
                raise ValueError('Targets require valid symbols; use __RESERVE__ for other funds')
            if key == '__RESERVE__' and info.field_name == 'investment_targets':
                raise ValueError('__RESERVE__ belongs only to total-assets targets')
            if key in result or not math.isfinite(weight) or not 0 <= weight <= 100:
                raise ValueError('Targets must be unique finite percentages from 0 to 100')
            result[key] = weight
        if result and abs(sum(result.values()) - 100) > 1e-6:
            raise ValueError('Target percentages must sum to 100')
        return result


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS allocation_plan(
        id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL,
        revision INTEGER NOT NULL, updated_at TEXT NOT NULL)''')


def read_plan(con):
    schema(con)
    row = con.execute('SELECT * FROM allocation_plan WHERE id=1').fetchone()
    return {**(json.loads(row['payload']) if row else AllocationPlan().model_dump(mode='json')),
            'revision': row['revision'] if row else 0,
            'updated_at': row['updated_at'] if row else None}


@router.get('/api/allocation')
def allocation(actor: str = Depends(authorize)):
    with db() as con:
        return read_plan(con)


def validate_change(plan: AllocationPlan, expected_revision: int, key: str):
    """Use identical input validation for MCP previews and committed changes."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a non-negative integer')
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ValueError('A unique idempotency_key of at most 200 characters is required')
    if plan.reserve_amount is not None and plan.reserve_as_of is None:
        raise ValueError('reserve_as_of is required with reserve_amount')
    if plan.reserve_as_of and plan.reserve_as_of > date.today():
        raise ValueError('Reserve balance date cannot be in the future')
    if plan.reserve_amount is None and plan.reserve_as_of is not None:
        raise ValueError('reserve_as_of requires reserve_amount')


def save_plan(plan: AllocationPlan, expected_revision: int, key: str, actor='mcp'):
    """Full replacement with revision check and retry-safe identity; no HTTP write route."""
    validate_change(plan, expected_revision, key)
    payload = plan.model_dump(mode='json')
    identity = json.dumps(['allocation', payload, expected_revision, actor], sort_keys=True)
    with db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        previous = con.execute('SELECT payload,result FROM requests WHERE key=?', (key,)).fetchone()
        if previous:
            if previous['payload'] != identity:
                raise HTTPException(409, 'Idempotency key already used with different data')
            return json.loads(previous['result'])
        before = read_plan(con)
        if before['revision'] != expected_revision:
            raise HTTPException(409, 'Allocation changed; read the latest plan before updating')
        stamp = datetime.now(timezone.utc).isoformat()
        con.execute('''INSERT INTO allocation_plan VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
            payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at''',
            (json.dumps(payload), before['revision'] + 1, stamp))
        result = read_plan(con)
        con.execute('INSERT INTO audit(actor,action,details) VALUES(?,?,?)',
                    (actor, 'allocation', json.dumps({'before': before, 'after': result})))
        con.execute('INSERT INTO requests VALUES(?,?,?)', (key, identity, json.dumps(result)))
        return result
