"""Local MCP server with explicit read/write scope. Process access to the database is the authority."""
from mcp.server.fastmcp import FastMCP
from apps.api.main import summary, trades
from packages.providers import get_quote

mcp = FastMCP('Accufolio')

@mcp.tool()
def get_summary(symbol: str = 'QQQM') -> dict:
    """Get holdings, costs, valuation and XIRR for a symbol, including data-source health."""
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
def add_trade(symbol: str, trade_date: str, shares: float, price: float, idempotency_key: str, note: str = '', dry_run: bool = True, side: str = 'buy') -> dict:
    """Preview or record a buy or sell. Requires write scope and a unique retry-safe key."""
    from apps.api.main import Trade, mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    trade = Trade(symbol=symbol, trade_date=trade_date, shares=shares, price=price, note=note, side=side)
    if dry_run: return {'preview':trade.model_dump(mode='json')}
    return mutate_trade('add', trade, None, 'mcp', idempotency_key)

@mcp.tool()
def update_trade(trade_id: int, symbol: str, trade_date: str, shares: float, price: float, idempotency_key: str, note: str = '', dry_run: bool = True, side: str = 'buy') -> dict:
    """Preview or replace a trade. Changes are audited and can be undone."""
    from apps.api.main import Trade, mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    trade = Trade(symbol=symbol, trade_date=trade_date, shares=shares, price=price, note=note, side=side)
    if dry_run: return {'trade_id':trade_id,'preview':trade.model_dump(mode='json')}
    return mutate_trade('update',trade,trade_id,'mcp',idempotency_key)

@mcp.tool()
def delete_trade(trade_id: int, idempotency_key: str, dry_run: bool = True) -> dict:
    """Soft-delete a transaction, preserving audit history."""
    from apps.api.main import mutate_trade
    require_write()
    if not idempotency_key: raise ValueError('idempotency_key is required')
    if dry_run: return {'preview':{'delete_trade':trade_id}}
    return mutate_trade('delete',None,trade_id,'mcp',idempotency_key)

@mcp.tool()
def undo_trade_change(audit_id: int) -> dict:
    """Undo a trade action if the record has not changed since that action."""
    from apps.api.extended import undo
    require_write()
    return undo(audit_id,actor='mcp')

@mcp.tool()
def list_alerts() -> list[dict]:
    """Read recent price and data-source alerts."""
    from apps.api.extended import alerts
    return alerts(actor='mcp')

@mcp.tool()
def get_health() -> dict:
    """Check database health and last scan time."""
    from apps.api.extended import health
    return health(actor='mcp')

@mcp.tool()
def set_watchlist(symbol: str, threshold: float = 2) -> dict:
    """Set a symbol's absolute daily price-change alert threshold in percent."""
    from apps.api.extended import Watch, set_watchlist as save
    require_write()
    return save(Watch(symbol=symbol,threshold=threshold), actor='mcp')

@mcp.tool()
def trigger_scan() -> list[dict]:
    """Scan the watchlist immediately and persist any new alerts."""
    from apps.api.extended import scan_all
    require_write()
    return scan_all()

if __name__ == '__main__':
    mcp.run(transport='stdio')
