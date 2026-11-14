"""Final schema initializer. Existing incompatible databases require explicit replacement."""
from pathlib import Path

ACCOUNT_KINDS = ('broker', 'bank', 'ewallet', 'provident', 'other')
ACCOUNT_PURPOSES = ('investing', 'savings', 'emergency', 'spending', 'housing_fund')
CASH_KINDS = ('balance_open', 'deposit', 'withdraw', 'interest', 'dividend', 'fee',
              'fx_in', 'fx_out', 'transfer_in', 'transfer_out')
REQUIRED_TABLES = {'accounts','cash_entries','trades','fx_rates','settings','allocation_targets',
                   'allocation_state','audit','requests','reversals'}


def _ready(con):
    """Read-only inspection must not start a transaction that later needs upgrading."""
    # Observe the publication marker first. A concurrent first initializer may
    # commit between these reads; an old table list paired with its NEW marker
    # would otherwise falsely report a damaged database. Version 0 simply takes
    # the serialized recheck in initialize(), even if tables appear meanwhile.
    version = con.execute('PRAGMA user_version').fetchone()[0]
    existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, required in {
        'trades': {'account_id','currency','fee','strategy'},
        'accounts': {'kind','purpose','currency','revision'},
        'cash_entries': {'entry_date','reverses_entry_id','transfer_group'},
    }.items():
        if table in existing and not required.issubset({r[1] for r in con.execute('PRAGMA table_info('+table+')')}):
            raise RuntimeError('Existing database is not Accufolio schema v2. Back it up and adapt it explicitly before starting.')
    if version == 2:
        if not REQUIRED_TABLES.issubset(existing):
            raise RuntimeError('Existing schema v2 database is incomplete. Restore or adapt it before starting.')
        return True
    if version not in (0, 2):
        raise RuntimeError('Unsupported database schema version; adapt the database explicitly before starting.')
    return False


def initialize(con):
    """Create final tables once, without write-locking every ordinary database read.

    First-time initialization obtains the writer lock before reading schema.
    Concurrent starters then wait and recheck rather than upgrading competing
    read transactions into writers (which SQLite cannot safely serialize).
    """
    if _ready(con):
        return
    if con.in_transaction:
        raise RuntimeError('Schema v2 initialization requires an idle connection')
    schema = Path(__file__).resolve().parents[2] / 'sql' / 'schema.sql'
    con.execute('BEGIN IMMEDIATE')
    try:
        if not _ready(con):
            for statement in schema.read_text(encoding='utf-8').split(';'):
                if statement.strip():con.execute(statement)
            con.execute('PRAGMA user_version=2')
        con.commit()
    except Exception:
        con.rollback()
        raise
