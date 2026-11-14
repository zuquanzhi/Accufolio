"""Local MCP server with explicit read/write scope. Process access to the database is the authority."""
from mcp.server.fastmcp import FastMCP
from apps.api.main import summary, trades
from packages.providers import get_quote

mcp = FastMCP('Accufolio')

@mcp.tool()
def get_summary(symbol: str) -> dict:
    """Single-symbol holdings/cost/valuation/XIRR. Use get_dashboard for the whole account."""
    return summary(symbol=symbol, actor='mcp')

@mcp.tool()
def list_trades(symbol: str = '') -> list[dict]:
    """List recorded buy and sell transactions, optionally filtered by symbol."""
    rows = trades(actor='mcp')
    return [row for row in rows if not symbol or row['symbol'] == symbol.upper()]

@mcp.tool()
def get_market_quote(symbol: str) -> dict:
    """Get a quote and clearly report any provider failure."""
    result = get_quote(symbol)
    return {'quote': result.quote.__dict__ if result.quote else None,
            'degraded': result.degraded, 'errors': list(result.errors)}

def require_write():
    import os
    if os.environ.get('ACCUFOLIO_MCP_SCOPE', 'read') != 'write':
        raise PermissionError('MCP write scope is disabled')

@mcp.tool()
def add_trade(symbol: str, trade_date: str, shares: float, price: float, idempotency_key: str, note: str = '', dry_run: bool = True, side: str = 'buy', account_id: int = 1, currency: str = 'USD', fee: float = 0, strategy: str = 'manual') -> dict:
    """Preview or record a buy or sell. Requires write scope and a unique retry-safe key."""
    from apps.api.main import Trade, mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    trade = Trade(symbol=symbol, trade_date=trade_date, shares=shares, price=price, note=note, side=side,account_id=account_id,currency=currency,fee=fee,strategy=strategy)
    return mutate_trade('add', trade, None, 'mcp', idempotency_key, dry_run)

@mcp.tool()
def update_trade(trade_id: int, symbol: str, trade_date: str, shares: float, price: float, idempotency_key: str, note: str = '', dry_run: bool = True, side: str = 'buy', account_id: int = 1, currency: str = 'USD', fee: float = 0, strategy: str = 'manual') -> dict:
    """Preview or replace a trade. Changes are audited and can be undone."""
    from apps.api.main import Trade, mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    trade = Trade(symbol=symbol, trade_date=trade_date, shares=shares, price=price, note=note, side=side,account_id=account_id,currency=currency,fee=fee,strategy=strategy)
    return mutate_trade('update',trade,trade_id,'mcp',idempotency_key,dry_run)

@mcp.tool()
def delete_trade(trade_id: int, idempotency_key: str, dry_run: bool = True) -> dict:
    """Soft-delete a transaction, preserving audit history."""
    from apps.api.main import mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    return mutate_trade('delete',None,trade_id,'mcp',idempotency_key,dry_run)

@mcp.tool()
def undo_trade_change(audit_id: int) -> dict:
    """Undo a trade action if the record has not changed since that action."""
    from apps.api.extended import undo
    require_write()
    return undo(audit_id,actor='mcp')

@mcp.tool()
def get_health() -> dict:
    """Check database integrity, automatic backup status and maintenance heartbeat."""
    from apps.api.extended import health
    return health(actor='mcp')

@mcp.tool()
def get_dashboard(period: str = '1y') -> dict:
    """Read the whole investment portfolio, allocation weights and reconstructed history."""
    from apps.api.dashboard import dashboard
    return dashboard(period=period, actor='mcp')

@mcp.tool()
def get_allocation_targets() -> dict:
    """Read percentage targets for securities and named net-worth accounts."""
    from apps.api.allocation import allocation
    return allocation(actor='mcp')

@mcp.tool()
def set_allocation_targets(scope: str, targets: dict[str, float], expected_revision: int,
                           idempotency_key: str, dry_run: bool = True) -> dict:
    """Replace one scope's weights, keeping the other. No balance aggregate is stored.

    scope=securities keys are stock symbols; net_worth keys are account:<id>.
    Nonempty weights must total100. Read latestrevision, preview then submit.
    """
    from apps.api.allocation import save_targets
    require_write()
    return save_targets(scope, targets, expected_revision, idempotency_key, dry_run)

@mcp.tool()
def get_assets() -> dict:
    """Net assets from securities and explicit per-account cash balances, with FX dates."""
    from apps.api.accounts import assets
    return assets(actor='mcp')

@mcp.tool()
def list_accounts() -> list[dict]:
    """List named accounts, currency, purpose, derived cash balance and revision."""
    from apps.api.accounts import list_accounts as read
    return read(actor='mcp')

@mcp.tool()
def list_cash_entries(account_id: int) -> list[dict]:
    """Read the cash journal that makes up an account balance."""
    from apps.api.accounts import list_cash_entries as read
    return read(account_id,actor='mcp')

@mcp.tool()
def set_account(name: str, kind: str, purpose: str, currency: str,
                expected_revision: int, idempotency_key: str, account_id: int | None = None,
                institution: str = '', opened_on: str | None = None,
                archived: bool = False, note: str = '', dry_run: bool = True) -> dict:
    """Create/replace account metadata. No balance field; amounts come from cash entries.

    kind: broker/bank/ewallet/provident/other; purpose: investing/savings/emergency/
    spending/housing_fund. One currency per account. A new account uses revision0.
    Read latest metadata before replacement; retain fields you want to keep.
    """
    from apps.api.accounts import Account, save_account
    require_write()
    return save_account(Account(name=name,kind=kind,purpose=purpose,currency=currency,
                        institution=institution,opened_on=opened_on,archived=archived,note=note),
                        account_id,expected_revision,idempotency_key,dry_run)

@mcp.tool()
def add_cash_entry(account_id: int, entry_date: str, kind: str, amount: float, currency: str,
                   expected_revision: int, idempotency_key: str, symbol: str | None = None,
                   note: str = '', dry_run: bool = True) -> dict:
    """Append one signed cash entry, preview by default. Account revision is required.

    Positive: balance_open/deposit/interest/dividend; negative: withdraw/fee.
    Use transfer_cash for both transfer/FX legs atomically. Securities records
    do not automatically settle cash; record actual cash movements explicitly.
    For securities returns, record actual net dividends with the original
    brokerage account and symbol. Link trade cash with set_trade_settlement.
    """
    from apps.api.accounts import CashEntry, add_cash_entry as save
    require_write()
    return save(CashEntry(account_id=account_id,entry_date=entry_date,kind=kind,amount=amount,
                         currency=currency,symbol=symbol,note=note),expected_revision,idempotency_key,dry_run)

@mcp.tool()
def transfer_cash(source_id: int, destination_id: int, source_amount: float,
                  destination_amount: float, entry_date: str, source_revision: int,
                  destination_revision: int, idempotency_key: str,
                  note: str = '', dry_run: bool = True) -> dict:
    """Record both account transfer legs atomically; explicit actual amounts set the FX rate."""
    from apps.api.accounts import transfer_cash as save
    require_write()
    return save(source_id,destination_id,source_amount,destination_amount,entry_date,
                source_revision,destination_revision,idempotency_key,note,dry_run)

@mcp.tool()
def reverse_cash_entry(entry_id: int, entry_date: str, expected_revision: int,
                       idempotency_key: str, note: str = '', dry_run: bool = True) -> dict:
    """Correct an unpaired cash entry with a linked opposite entry; never erase its history."""
    from apps.api.accounts import reverse_cash_entry as save
    require_write()
    return save(entry_id,entry_date,expected_revision,idempotency_key,note,dry_run)

@mcp.tool()
def get_settings() -> dict:
    """Read shared display defaults. Device-specific display choices can override them."""
    from apps.api.preferences import get_settings as read
    return read()

@mcp.tool()
def set_settings(display_currency: str, market_timezone: str, expected_revision: int,
                 idempotency_key: str, dry_run: bool = True) -> dict:
    """Replace shared display preferences. US market calculations always use Eastern Time."""
    from apps.api.preferences import set_settings as save
    require_write()
    return save(display_currency,market_timezone,expected_revision,idempotency_key,dry_run)

@mcp.tool()
def list_trade_settlements() -> list[dict]:
    """Read explicit links from securities trades to actual cash movements."""
    from apps.api.settlements import list_settlements
    return list_settlements(actor='mcp')


@mcp.tool()
def set_trade_settlement(trade_id: int, cash_entry_id: int | None, expected_revision: int,
                         idempotency_key: str, dry_run: bool = True) -> dict:
    """Link a trade to its recorded net cash settlement, preview by default.

    Use the same account, currency and net amount including trading fees.
    Settlement cannot predate the trade. Each cash entry settles one trade.
    Pass cash_entry_id=null to unlink before correcting either source record.
    This tool never creates cash. Read account revision immediately beforehand.
    """
    from apps.api.settlements import set_trade_settlement as save
    require_write()
    return save(trade_id,cash_entry_id,expected_revision,idempotency_key,dry_run)


@mcp.tool()
def get_asset_history(period: str = '1y', currency: str = 'USD') -> dict:
    """Historical all-account value, flow-adjusted NAV, return and drawdown.

    Read coverage and unmatched_trade_ids before interpreting performance.
    period: 1m/3m/1y/all; currency: USD/CNY/HKD/EUR. No financial writes.
    """
    from apps.api.asset_history import asset_history
    return asset_history(period=period,currency=currency,actor='mcp')


if __name__ == '__main__':
    mcp.run(transport='stdio')
