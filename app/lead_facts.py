"""Conservative, local extraction of source-reported facts, not underwriting.

Only retained snippet/page text is evidence (never titles, queries, or existing
numeric fields). Unsupported wording, ranges, projections, conflicting values,
and oversized evidence remain unknown. No fetching or geographic inference is
performed. Quotes preserve source wording, including demographic geography.
"""

from copy import deepcopy
from decimal import Decimal
import ipaddress
import re
from urllib.parse import urlsplit

from app.listing_discovery import lead_kind, safe_public_url
from app.structured_properties import is_json_evidence, rental_price_context, structured_properties


MAX_TEXT = 8000
MAX_EXCERPTS = 12
MAX_CLAUSE = 600
UNITS = {
    "asking_price": "USD",
    "area_sqft": "sqft",
    "cap_rate": "%",
    "occupancy_pct": "%",
    "annual_noi": "USD/year",
    "asking_rent_psf": "USD/sqft",
    "population": "people",
    "median_household_income": "USD",
}
_DEMOGRAPHICS = {"population", "median_household_income"}
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_VALUE = rf"(?P<number>{_NUMBER})(?:\s*(?P<scale>million|thousand|billion|[mkb])(?![a-z]))?"
_MONEY = rf"(?:\$\s*{_VALUE}|USD\s+{_VALUE.replace('number', 'usd_number').replace('scale', 'usd_scale')})"
_JOIN = r"\s*(?::|=|\bis\b|\bof\b)?\s*"
_SF = r"(?:sq\.?\s*ft\.?|square\s+feet|sf)\b"
_PRICE = r"(?:asking\s+price|sale\s+price|list(?:ing)?\s+price|offered\s+at)"
_CAP = r"cap(?:italization)?\s*(?:rate)?"
_OCCUPANCY = r"(?:occupancy(?:\s+rate)?|occupied|leased)"
_ANNUAL_NOI = r"annual\s*(?:NOI|net\s+operating\s+income)"
_NOI = r"(?:NOI|net\s+operating\s+income)"
_RENT = r"asking\s+rent(?:al\s+rate)?"
_INCOME = r"median\s+household\s+income"


def _patterns(*expressions):
    # Boundaries prevent partial matches inside malformed numbers or identifiers.
    return tuple(re.compile(r"(?<![\w.,$+\-])(?:" + expr + r")(?![\w%]|[.,]\d)", re.I)
                 for expr in expressions)


_PATTERNS = {
    "asking_price": _patterns(rf"{_PRICE}{_JOIN}{_MONEY}", rf"{_MONEY}\s+{_PRICE}"),
    "area_sqft": _patterns(rf"{_VALUE}\s*{_SF}",
                           rf"(?<!/)(?:Gross|Rentable|Building)\s+SF{_JOIN}{_VALUE}"),
    "cap_rate": _patterns(rf"{_CAP}{_JOIN}{_VALUE}\s*%", rf"{_VALUE}\s*%\s*{_CAP}"),
    "occupancy_pct": _patterns(rf"{_OCCUPANCY}{_JOIN}{_VALUE}\s*%",
                               rf"{_VALUE}\s*%\s*{_OCCUPANCY}"),
    "annual_noi": _patterns(rf"{_ANNUAL_NOI}{_JOIN}{_MONEY}",
                            rf"{_NOI}{_JOIN}{_MONEY}\s*(?:/\s*(?:yr|year)|per\s+year|annually)\b"),
    "asking_rent_psf": _patterns(rf"{_RENT}{_JOIN}{_MONEY}\s*(?:/|per\s+){_SF}"
                                 r"(?:\s*(?:/|per\s+)(?P<period>year|yr|month|mo)\b)?"),
    "population": _patterns(rf"population{_JOIN}{_VALUE}"),
    "median_household_income": _patterns(rf"{_INCOME}{_JOIN}{_MONEY}"),
}
_CATALOG = re.compile(
    r"\b(?:\d+\s+(?:properties|listings|results)|multiple\s+(?:properties|listings)|"
    r"(?:properties|listings)\s+for\s+(?:sale|lease)|search\s+results|"
    r"(?:shopping\s+cent(?:er|re)s|strip\s+(?:cent(?:er|re)s|malls)|"
    r"retail\s+(?:cent(?:er|re)s|buildings)|malls)\s+for\s+(?:sale|lease)|"
    r"other\s+(?:properties|listings)|similar\s+(?:properties|listings)|"
    r"portfolio|catalog|(?:listing|property)\s+(?:directory|index))\b", re.I)
_INDEX_PATH = re.compile(r"/(?:listings|catalog|inventory|search|for-sale|for-lease|index(?:\.html?)?)"
                         r"(?:/|$)", re.I)
_INDEX_KINDS = {"index", "catalog", "directory", "listing_index", "listing_directory", "search_results"}
_UNCERTAIN = re.compile(
    r"\b(?:projected|projection|pro\s*forma|potential|estimated|estimate|approx(?:imately)?|"
    r"target|forecast|stabilized|up\s+to|starting|from|between|range|over|under|"
    r"at\s+least|at\s+most|more\s+than|less\s+than|not|formerly|previous|prior|"
    r"historical|was|could|would|may)\b|[~<>]", re.I)
_RANGE = re.compile(r"\d\s*(?:%|million|thousand|[mkb]|SF)?\s*(?:[-\u2013\u2014/+]|\bto\b|\bor\b)"
                    r"\s*\$?\s*\d|\d\s*(?:million|thousand|[mkb]|%)?\s*\+", re.I)
_AREA_OTHER = re.compile(r"\b(?:lot|land|suite|available|divisible|expansion|acre|acres)\b", re.I)
_FOREIGN_CURRENCY = re.compile(r"\b(?:CAD|AUD|NZD|HKD|SGD|EUR|GBP)\b|[\u20ac\u00a3]", re.I)
_PROPERTY_NAME = re.compile(r"\b(?:[A-Z][a-z]+\s+){1,3}(?:Plaza|Center|Centre|Mall|Shops)\b")
_ADDRESS = re.compile(r"\b\d{1,6}\s+(?:[A-Za-z]+\s+){1,4}"
                      r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Boulevard|Blvd|Lane|Ln)\b", re.I)
_ENTRY = re.compile(r"\b(?:property|listing)\s*(?:#\s*)?[A-Z0-9]+\s*:", re.I)
_PROPERTY_LINK = re.compile(r"https?://[^\s<>\"()]+/(?:properties|listing)/[^\s<>\"()]+", re.I)
_ALTERNATIVE = re.compile(r"\s*(?:,|/|and\b|or\b|to\b)\s*\$?\d", re.I)
_PER_UNIT = re.compile(r"\s*(?:/|per\s+)(?:sf|sq|square|acre|unit|month|mo)\b", re.I)
_TABLE_PAIR = re.compile(
    rf"^[ \t]*(?:Cap Rate|Gross SF|Rentable SF|Building SF|Annual NOI|Asking Price|Listing Price|Occupancy)"
    rf"[ \t]*\r?\n(?:[ \t]*\r?\n)*[ \t]*(?:\$|USD\s*)?{_NUMBER}"
    r"(?:[ \t]*(?:%|million|thousand|[mkb]))?[ \t]*$", re.I | re.M)


def _evidence(lead):
    """Reject over-budget inputs rather than hide conflicts by truncating them."""
    excerpts = lead.get("excerpts", [])
    if not isinstance(excerpts, list) or len(excerpts) > MAX_EXCERPTS:
        return None
    values = [lead.get("snippet"), lead.get("page_excerpt"), *excerpts]
    texts = []
    for value in values:
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            return None
        if value not in texts:
            texts.append(value)
    return texts


def _source_url(lead):
    supplied = [lead[key] for key in ("url", "source_url") if lead.get(key)]
    if not supplied:
        return None
    urls = [safe_public_url(value) for value in supplied]
    if any(url is None or "\\" in url for url in urls) or len(set(urls)) != 1:
        return None
    host = urlsplit(urls[0]).hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        # Reject legacy numeric IP forms which browsers may interpret as loopback.
        if re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*", host):
            return None
    return urls[0]


def _clauses(text):
    # Split prose while retaining exact substrings, not normalized/reconstructed text.
    clauses = [part.strip() for part in re.split(
        r"[;|\n]+|#{1,6}\s+|(?<=[.!?])\s+(?=[A-Z])|\band features\b", text)
        if part.strip()]
    # Provider Markdown frequently places a table label and value on separate
    # lines. Match within one excerpt, never across independently fetched text.
    for match in _TABLE_PAIR.finditer(text):
        preceding = text[:match.start()].rstrip().splitlines()
        if not preceding or not _UNCERTAIN.search(preceding[-1]):
            clauses.append(match.group().strip())
    return clauses


def _value(match):
    groups = match.groupdict()
    number = groups.get("number") or groups.get("usd_number")
    scale = (groups.get("scale") or groups.get("usd_scale") or "").lower()
    multiplier = {"": 1, "k": 1000, "thousand": 1000, "m": 1000000,
                  "million": 1000000, "b": 1000000000, "billion": 1000000000}[scale]
    value = Decimal(number.replace(",", "")) * multiplier
    # Bound pathological inputs and guarantee finite JSON-compatible numbers.
    if value > 10**15:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _facts(lead, *, isolated=False):
    facts = {key: {"value": None, "unit": unit, "quote": None,
                   "source_url": None, "status": "unknown"} for key, unit in UNITS.items()}
    texts = _evidence(lead)
    url = _source_url(lead)
    if not texts or not url:
        return facts
    title = lead.get("title") if isinstance(lead.get("title"), str) else ""
    combined = "\n".join(texts)
    property_source = ((isolated or (lead.get("lead_kind", "property_discovery") == "property_discovery"
                       and lead_kind(url) == "property_discovery"
                       and str(lead.get("source_kind", "")).lower() not in _INDEX_KINDS
                       and not _INDEX_PATH.search(urlsplit(url).path)))
                       and len(title) <= MAX_TEXT
                       and not _CATALOG.search(title)
                       and not any(_CATALOG.search(text) for text in texts)
                       and len(set(_PROPERTY_NAME.findall(combined))) < 2
                       and len(set(_ADDRESS.findall(combined))) < 2
                       and len(set(_ENTRY.findall(combined))) < 2
                       and len(set(_PROPERTY_LINK.findall(combined))) < 2)
    clauses = [clause for text in texts for clause in _clauses(text)]
    for key, patterns in _PATTERNS.items():
        if key not in _DEMOGRAPHICS and not property_source:
            continue
        candidates = []
        contexts = set()
        invalid = False
        for clause in clauses:
            for pattern in patterns:
                for match in pattern.finditer(clause):
                    value = _value(match)
                    tail = clause[match.end():]
                    if (len(clause) > MAX_CLAUSE or _UNCERTAIN.search(clause) or _RANGE.search(clause)
                            or value is None or (key in {"cap_rate", "occupancy_pct"} and value > 100)
                            or re.match(r"\s*[:=]", tail) or _ALTERNATIVE.match(tail)
                            or (key == "asking_price" and rental_price_context(clause, tail=tail))
                            or (key in {"asking_price", "annual_noi", "median_household_income"}
                                and _PER_UNIT.match(tail))
                            or (UNITS[key].startswith("USD") and _FOREIGN_CURRENCY.search(clause))
                            or (key == "annual_noi" and re.search(r"\bmonth(?:ly)?\b|/mo\b", clause, re.I))
                            or (key == "population" and not float(value).is_integer())
                            or (key == "area_sqft" and _AREA_OTHER.search(clause)
                                and not re.match(r"(?:Gross|Rentable|Building)\s+SF", match.group(), re.I))):
                        invalid = True
                        continue
                    unit = UNITS[key]
                    period = match.groupdict().get("period")
                    if period:
                        unit += "/year" if period.lower() in {"yr", "year"} else "/month"
                    candidates.append((value, unit, clause))
                    contexts.add((clause, match.start(), match.end()))
        identities = {(value, unit) for value, unit, _ in candidates}
        # Two geographic/year contexts cannot be collapsed into a single demographic.
        if key in _DEMOGRAPHICS and len(contexts) > 1:
            invalid = True
        if not invalid and len(identities) == 1:
            value, unit, quote = candidates[0]
            facts[key] = {"value": value, "unit": unit, "quote": quote,
                          "source_url": url, "status": "source_reported"}
    return facts


def enrich_leads(leads: list[dict]) -> list[dict]:
    """Return independent copies with all eight fact slots and exact citations.

    Percentages are percentage points (6, not .06). Rent periods are recorded
    only when explicit; bare $/SF is not annualized. Demographics have no assumed
    trade-area radius. Source-reported does not mean verified or current.
    """
    enriched = []
    for lead in leads:
        copied = deepcopy(lead)
        copied.pop("properties", None)  # Never trust previously populated children.
        texts = _evidence(lead)
        structured = texts and any(is_json_evidence(text) for text in texts)
        copied["facts"] = _facts({} if structured else lead)
        if structured and _source_url(lead):
            properties = structured_properties(lead, texts)
            if properties:
                copied["properties"] = properties
        enriched.append(copied)
    return enriched
