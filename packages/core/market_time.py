"""US equity dates use America/New_York and the exchange's regular session calendar."""
from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')


def et_today():
    return datetime.now(ET).date()


@lru_cache(maxsize=8)
def _calendar(first_year, last_year):
    import exchange_calendars
    return exchange_calendars.get_calendar('XNYS', start=f'{first_year}-01-01', end=f'{last_year}-12-31')


def session_days(start, end):
    if start > end:
        return []
    calendar = _calendar(start.year, end.year)
    return [stamp.date() for stamp in calendar.sessions_in_range(start.isoformat(), end.isoformat())]


def market_clock(now=None):
    """Separate calendar date, current session phase and last completed session.

    XNYS includes DST, weekends, exchange holidays, exceptional closures and
    early closes. Daily-price availability is checked separately by callers.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Market clock requires a timezone-aware timestamp')
    local = now.astimezone(ET)
    today = local.date()
    calendar = _calendar(today.year-1, today.year+1)
    session = calendar.date_to_session(today.isoformat(), direction='previous')
    close = calendar.session_close(session).to_pydatetime()
    opening = calendar.session_open(session).to_pydatetime()
    phase = 'closed'
    if session.date() == today:
        if now < opening:
            phase = 'premarket'
        elif now < close:
            phase = 'intraday'
        else:
            phase = 'afterhours'
    completed = session if now >= close else calendar.previous_session(session)
    return {'date': today.isoformat(), 'now': local.isoformat(), 'timezone': 'America/New_York',
            'calendar': 'XNYS', 'phase': phase, 'last_completed_session': completed.date().isoformat()}
