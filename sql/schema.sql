-- Final schema. No personal account balances or sample exchange rates.
CREATE TABLE IF NOT EXISTS accounts(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('broker','bank','ewallet','provident','other')),
 purpose TEXT NOT NULL CHECK(purpose IN ('investing','savings','emergency','spending','housing_fund')),
 currency TEXT NOT NULL CHECK(length(currency)=3), institution TEXT NOT NULL DEFAULT '',
 opened_on TEXT, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),
 note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cash_entries(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), entry_date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('balance_open','deposit','withdraw','interest','dividend','fee','fx_in','fx_out','transfer_in','transfer_out')),
 amount REAL NOT NULL, currency TEXT NOT NULL CHECK(length(currency)=3), fx_rate REAL,
 counter_account_id INTEGER REFERENCES accounts(id), symbol TEXT, note TEXT NOT NULL DEFAULT '',
 reverses_entry_id INTEGER REFERENCES cash_entries(id), transfer_group TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(kind NOT IN ('fx_in','fx_out') OR (fx_rate IS NOT NULL AND fx_rate>0))
);
CREATE INDEX IF NOT EXISTS cash_entries_account_date ON cash_entries(account_id,entry_date);
CREATE UNIQUE INDEX IF NOT EXISTS cash_one_reversal ON cash_entries(reverses_entry_id) WHERE reverses_entry_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS trades(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id),
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, side TEXT NOT NULL DEFAULT 'buy' CHECK(side IN('buy','sell')),
 shares REAL NOT NULL CHECK(shares>0), price REAL NOT NULL CHECK(price>0),fee REAL NOT NULL DEFAULT 0 CHECK(fee>=0),
 currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency)=3),
 strategy TEXT NOT NULL DEFAULT 'manual' CHECK(strategy IN('dca','dip_add','rebalance','manual')),
 note TEXT NOT NULL DEFAULT '',deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN(0,1))
);
CREATE TABLE IF NOT EXISTS fx_rates(
 base TEXT NOT NULL,quote TEXT NOT NULL,rate_date TEXT NOT NULL,rate REAL NOT NULL CHECK(rate>0),
 source TEXT NOT NULL,fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(base,quote,rate_date)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS allocation_targets(
 scope TEXT NOT NULL CHECK(scope IN('securities','net_worth')),key TEXT NOT NULL,
 weight REAL NOT NULL CHECK(weight>=0 AND weight<=100),updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(scope,key)
);
CREATE TABLE IF NOT EXISTS allocation_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,actor TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist(symbol TEXT PRIMARY KEY,threshold REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY,ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,symbol TEXT NOT NULL,price REAL,change_percent REAL,error TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,symbol TEXT NOT NULL,message TEXT NOT NULL,dedupe TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS reversals(audit_id INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO accounts(id,name,kind,purpose,currency) VALUES(1,'美股账户','broker','investing','USD');
