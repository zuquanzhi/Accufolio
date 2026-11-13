"""ECB reference exchange rates for display, never for trade settlement.

The ECB quotes currency units per euro. Dividing each rate by EUR/USD
produces the currency units per USD needed by the USD-denominated ledger.
"""
from datetime import date, datetime, timezone
import math
import ssl
from urllib.request import Request, urlopen
from xml.etree import ElementTree
import certifi


SOURCE_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
HISTORY_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml'
SOURCE_PAGE = 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html'
SUPPORTED = ('USD', 'CNY', 'HKD', 'EUR')
MAX_RESPONSE_BYTES = 64 * 1024
MAX_HISTORY_BYTES = 20 * 1024 * 1024
MAX_HISTORY_DAYS = 15000
ECB_NAMESPACE = 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'


def validate_rates(payload: dict, today: date | None = None) -> dict:
    """Validate provider output and persisted caches before any display use."""
    if not isinstance(payload, dict) or payload.get('base') != 'USD':
        raise ValueError('Reference rates must use USD as base')
    as_of = date.fromisoformat(payload['as_of'])
    today = today or datetime.now(timezone.utc).date()
    if as_of.year < 1999 or as_of > today:
        raise ValueError('Invalid reference-rate date')
    rates = payload.get('rates')
    if not isinstance(rates, dict):
        raise ValueError('Missing reference rates')
    cleaned = {}
    for currency in SUPPORTED:
        value = rates.get(currency)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Reference rates must be finite positive numbers')
        cleaned[currency] = float(value)
    if cleaned['USD'] != 1:
        raise ValueError('USD base rate must equal one')
    return {'base': 'USD', 'rates': cleaned, 'as_of': as_of.isoformat()}


def ecb_rates(fetch=None) -> dict:
    """Read one bounded, dated ECB XML snapshot without third-party API keys."""
    if fetch is None:
        def fetch(target):
            request = Request(target, headers={'User-Agent': 'Accufolio', 'Accept': 'application/xml,text/xml'})
            with urlopen(request, timeout=10, context=ssl.create_default_context(cafile=certifi.where())) as response:
                return response.read(MAX_RESPONSE_BYTES + 1)
    raw = fetch(SOURCE_URL)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Reference-rate response exceeds size limit')
    # The ECB feed is UTF-8 and has no DTD. Reject declarations before parsing
    # rather than allowing entities in network-supplied XML.
    text = raw.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('XML declarations are not supported')
    root = ElementTree.fromstring(text)
    snapshots = [node for node in root.iter(f'{{{ECB_NAMESPACE}}}Cube') if 'time' in node.attrib]
    if len(snapshots) != 1:
        raise ValueError('Expected one daily reference-rate snapshot')
    snapshot = snapshots[0]
    euro_rates = {'EUR': 1.0}
    for node in snapshot:
        currency = node.get('currency')
        if currency not in SUPPORTED:
            continue
        if currency in euro_rates:
            raise ValueError('Duplicate reference rate')
        rate = float(node.get('rate', 'nan'))
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid reference rate')
        euro_rates[currency] = rate
    if set(euro_rates) != set(SUPPORTED):
        raise ValueError('Missing display-currency reference rates')
    rates = {currency: value / euro_rates['USD'] for currency, value in euro_rates.items()}
    return validate_rates({'base': 'USD', 'rates': rates, 'as_of': snapshot.get('time')})


def ecb_history(fetch=None) -> list[dict]:
    """Dated USD-base snapshots, including partial early currency coverage.

    A missing currency in 1999 is unknown; later rates never fill that gap.
    The official archive is bounded before parsing and has no DTD/entities.
    """
    if fetch is None:
        def fetch(target):
            request = Request(target, headers={'User-Agent': 'Accufolio', 'Accept': 'application/xml,text/xml'})
            with urlopen(request, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as response:
                return response.read(MAX_HISTORY_BYTES + 1)
    raw = fetch(HISTORY_URL)
    if not isinstance(raw, bytes) or len(raw) > MAX_HISTORY_BYTES:
        raise ValueError('Historical reference-rate response exceeds size limit')
    text = raw.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('XML declarations are not supported')
    root = ElementTree.fromstring(text)
    snapshots = [node for node in root.iter(f'{{{ECB_NAMESPACE}}}Cube') if 'time' in node.attrib]
    if not 1 <= len(snapshots) <= MAX_HISTORY_DAYS:
        raise ValueError('Invalid historical reference-rate count')
    today = datetime.now(timezone.utc).date()
    days, result = set(), []
    for snapshot in snapshots:
        day = date.fromisoformat(snapshot.get('time'))
        if day < date(1999, 1, 1) or day > today or day in days:
            raise ValueError('Invalid or duplicate historical rate date')
        days.add(day)
        euro_rates = {'EUR': 1.0}
        for node in snapshot:
            currency = node.get('currency')
            if currency not in SUPPORTED:
                continue
            if currency in euro_rates:
                raise ValueError('Duplicate historical reference rate')
            value = float(node.get('rate', 'nan'))
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid historical reference rate')
            euro_rates[currency] = value
        if 'USD' not in euro_rates:
            raise ValueError('Historical snapshot lacks USD base')
        result.append({'base': 'USD', 'as_of': day.isoformat(),
                       'rates': {currency: value / euro_rates['USD'] for currency, value in euro_rates.items()}})
    return sorted(result, key=lambda item: item['as_of'])
