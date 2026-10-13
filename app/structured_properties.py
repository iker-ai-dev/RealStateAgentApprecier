"""Bounded schema.org directory extraction from retained JSON, never summaries.

Descriptions are isolated from names (street-number ranges are not metric
ranges). Offer prices require explicit USD; neither addresses nor sibling dollar
signs establish currency. Quotes are exact retained JSON object substrings.
All citations point to the retained source, even when a child has its own URL.
"""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation


MAX_NODES = 512
MAX_DEPTH = 12
MAX_PROPERTIES = 64
_PROPERTY_TYPES = {"Product", "RealEstateListing", "Place", "Residence", "Accommodation", "RetailBuilding"}
_NON_PROPERTY_TYPES = {"Organization", "Person", "WebSite", "BreadcrumbList"}
_FOR_RENT = re.compile(r"\bfor\s+(?:lease|rent)\b", re.I)
_PERIOD = r"(?:monthly|weekly|daily|yearly|annually|(?:/|per\s+)(?:mo(?:nth)?|yr|year|week|day|sf|sqft|square\s+foot))\b"
_PERIOD_TAIL = re.compile(r"\s*" + _PERIOD, re.I)
_PERIODIC_MONEY = re.compile(
    r"(?:\$\s*|USD\s+)(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(million|thousand|[mk])?\s*" + _PERIOD, re.I)
_RENTAL_FUNCTION = re.compile(r"(?:^|[/#\s])(?:LeaseOut|RentOut|RentAction|Rent|Lease|Rental)$", re.I)
_PRICE_UNITS = {"priceUnit", "unitCode", "unitText", "unitOfMeasurement",
                "referenceQuantity", "billingDuration", "billingIncrement", "priceSpecification"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def rental_price_context(text, *, tail=None, price=None):
    if _FOR_RENT.search(text):
        return True
    if tail is not None and _PERIOD_TAIL.match(tail):
        return True
    if price is not None:
        for match in _PERIODIC_MONEY.finditer(text):
            scale = (match[2] or "").lower()
            value = Decimal(match[1].replace(",", "")) * {
                "": 1, "k": 1000, "thousand": 1000, "m": 1000000, "million": 1000000}[scale]
            if value == price:
                return True
    return False


def _rental_context(node, price=None):
    """Sale prices cannot be established from rental or unit-priced records."""
    if isinstance(node, list):
        return any(_rental_context(value, price) for value in node)
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if key in _PRICE_UNITS and value is not None:
            return True
        if key in {"name", "description", "businessFunction", "category", "@type"}:
            if isinstance(value, str) and rental_price_context(value, price=price):
                return True
            if key in {"businessFunction", "category", "@type"}:
                values = value if isinstance(value, list) else [value]
                if any(isinstance(v, str) and _RENTAL_FUNCTION.search(v) for v in values):
                    return True
        if key in {"offers", "item", "mainEntity"} and _rental_context(value, price):
            return True
    return False


def _types(node):
    values = node.get("@type", [])
    values = values if isinstance(values, list) else [values]
    return {value.rsplit("/", 1)[-1] for value in values if isinstance(value, str)}


def is_json_evidence(text):
    text = text.lstrip()
    # A Markdown link is prose, not a malformed JSON array. Still recognize
    # truncated JSON containers so they cannot fall through to regex extraction.
    return text.startswith("{") or bool(re.match(
        r'^\[\s*(?:$|[\[\]{"\d-]|(?:true|false|null)\b)', text))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON value")


def _bounded(root):
    stack = [(root, 0)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Structured evidence exceeds bounds")
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)


def _quote(text, obj):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if value == obj:
            return text[match.start():end]
    raise ValueError("Missing original JSON quote")


def _entries(root):
    graph = root.get("@graph", []) if isinstance(root, dict) else root
    refs = {}
    for node in graph if isinstance(graph, list) else []:
        if isinstance(node, dict) and isinstance(node.get("@id"), str):
            key = node["@id"]
            if key in refs:
                raise ValueError("Ambiguous graph reference")
            refs[key] = node

    def resolve(node):
        if isinstance(node, dict) and set(node) == {"@id"}:
            return refs.get(node["@id"], node)
        return node

    visits = 0

    def walk(node, depth=0, path=(), context=()):
        nonlocal visits
        visits += 1
        if visits > MAX_NODES:
            raise ValueError("Excessive graph traversal")
        if depth > MAX_DEPTH:
            raise ValueError("Cyclic or deep schema graph")
        node = resolve(node)
        if isinstance(node, list):
            for index, child in enumerate(node):
                yield from walk(child, depth + 1, (*path, index), context)
        elif isinstance(node, dict):
            if _types(node) & _NON_PROPERTY_TYPES:
                return
            if "itemListElement" in node:
                entries = node["itemListElement"]
                if not isinstance(entries, list) or len(entries) > MAX_PROPERTIES:
                    raise ValueError("Invalid or excessive item list")
                for index, entry in enumerate(entries):
                    entry = resolve(entry)
                    if isinstance(entry, dict):
                        yield (resolve(entry.get("item", entry)), (*path, "itemListElement", index),
                               (*context, entry))
            elif _types(node) & {"Product", "RealEstateListing"}:
                context = (*context, node)
                yield node, path, context
            elif "ListItem" in _types(node) and "item" in node:
                yield resolve(node["item"]), (*path, "item"), (*context, node)
            for key in ("mainEntity", "@graph"):
                if key in node:
                    yield from walk(node[key], depth + 1, (*path, key), context)

    return walk(root)


def structured_properties(lead, texts):
    from app.lead_facts import _facts, _source_url, _UNCERTAIN, MAX_EXCERPTS, MAX_TEXT

    source = _source_url(lead)
    groups = {}
    fingerprints = {}
    try:
        for text_index, text in enumerate(texts):
            if not is_json_evidence(text):
                continue
            root = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
            _bounded(root)
            for node, path, context in _entries(root):
                if not isinstance(node, dict):
                    continue
                if _types(node) and not _types(node) & (_PROPERTY_TYPES | {"ListItem"}):
                    continue
                title = node.get("name")
                if not isinstance(title, str) or not title.strip() or len(title) > 600:
                    continue
                direct_url = _source_url({"url": node.get("url")})
                url = direct_url or source
                explicit_id = node.get("@id")
                explicit_id = explicit_id if isinstance(explicit_id, str) and explicit_id.strip() else None
                address = node.get("address")
                address = address if isinstance(address, (str, dict)) and address else None
                if explicit_id or address:
                    identity = _canonical(["record", explicit_id, address])
                elif direct_url and direct_url != source:
                    identity = _canonical(["url", direct_url])
                else:
                    # A generic name and source fallback URL do not identify a
                    # property. Only exact nodes can deduplicate anonymous entries.
                    identity = _canonical(["entry", text_index, path])
                fingerprint = _canonical(node)
                identity = fingerprints.setdefault(fingerprint, identity)
                group = groups.setdefault(identity, {"title": title.strip(), "url": url,
                                                     "descriptions": {}, "offers": [], "invalid": False})
                if any(_rental_context(record) for record in (*context, node)):
                    group["invalid"] = True
                if len(groups) > MAX_PROPERTIES:
                    raise ValueError("Too many properties")
                description = node.get("description", "")
                if not isinstance(description, str):
                    raise ValueError("Invalid description")
                if is_json_evidence(description):
                    raise ValueError("Description is not property prose")
                if description and description not in group["descriptions"]:
                    group["descriptions"][description] = _quote(text, node)
                if len(group["descriptions"]) > MAX_EXCERPTS:
                    raise ValueError("Too many descriptions")
                if "offers" in node:
                    offers = node["offers"]
                    offers = offers if isinstance(offers, list) else [offers]
                    if not offers:
                        group["invalid"] = True
                    for offer in offers:
                        if not isinstance(offer, dict):
                            group["invalid"] = True
                            continue
                        price = offer.get("price")
                        currency = offer.get("priceCurrency")
                        quote = _quote(text, offer)
                        if (currency != "USD" or isinstance(price, bool)
                                or not isinstance(price, (str, int, float))
                                or not re.fullmatch(r"\d+(?:\.\d+)?", str(price))
                                or _UNCERTAIN.search(description)
                                or _UNCERTAIN.search(quote)
                                or (_types(offer) and _types(offer) != {"Offer"})
                                or any(k in offer for k in ("lowPrice", "highPrice", "priceSpecification"))):
                            group["invalid"] = True
                            continue
                        number = Decimal(str(price))
                        if not number.is_finite() or not 0 <= number <= 10**15:
                            group["invalid"] = True
                            continue
                        if any(_rental_context(record, number) for record in (*context, node)):
                            group["invalid"] = True
                            continue
                        group["offers"].append((int(number) if number == int(number) else float(number), quote))

        result = []
        for identity, group in groups.items():
            title, url = group["title"], group["url"]
            descriptions = group["descriptions"]
            facts = _facts({"url": source, "excerpts": list(descriptions)}, isolated=True)
            for fact in facts.values():
                if fact["quote"] is not None:
                    # Decoding can change escapes, whitespace, and Unicode.
                    # Cite the original bounded object, never reconstructed JSON.
                    fact["quote"] = next(raw for description, raw in descriptions.items()
                                         if fact["quote"] in description)
            unknown = _facts({})["asking_price"]
            values = {value for value, _ in group["offers"]}
            if facts["asking_price"]["value"] is not None:
                values.add(facts["asking_price"]["value"])
            # A description containing an invalid price must not be rescued by offers.
            price_text = any(re.search(r"asking\s+price|sale\s+price|list(?:ing)?\s+price|offered\s+at", d, re.I)
                             for d in descriptions)
            if (group["invalid"] or len(values) > 1
                    or (price_text and facts["asking_price"]["value"] is None)):
                facts["asking_price"] = unknown
            elif group["offers"]:
                value, quote = group["offers"][0]
                facts["asking_price"] = {"value": value, "unit": "USD", "quote": quote,
                                         "source_url": source, "status": "source_reported"}
            scope = _canonical([source, lead.get("id"), identity])
            child_id = "property-" + hashlib.sha256(scope.encode()).hexdigest()[:24]
            result.append({"id": child_id, "title": title, "url": url,
                           "summary": "\n".join(descriptions)[:MAX_TEXT] or title, "facts": facts})
        return result
    except (ValueError, TypeError, RecursionError, InvalidOperation):
        # Reject the entire structured evidence set rather than truncate conflicts.
        return []
