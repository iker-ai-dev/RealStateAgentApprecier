import copy
import json
import unittest

from app.lead_facts import MAX_EXCERPTS, MAX_TEXT, UNITS, enrich_leads
from app.structured_properties import MAX_PROPERTIES


SOURCE = "https://broker.example/search/strip-malls/"
SCREENSHOT = {"mainEntity": {"itemListElement": [
    {"name": "6172-6196 University Ave, San Diego, CA 92115",
     "description": "20,934 SF Retail Building Offered at $6,000,000 at a 7.25% Cap Rate in San Diego, CA 92115"},
    {"name": "3164-3194 Midway Dr, San Diego, CA 92110"},
    {"name": "4224-4228 El Cajon Blvd, San Diego, CA 92105"},
    {"name": "2483 Kettner Blvd, San Diego, CA 92101",
     "description": "1,610 SF Retail Building Offered at $1,050,000 in San Diego, CA 92101"},
    {"offers": [{"price": "545000"}],
     "name": "260 S Rancho Santa Fe Rd, San Marcos, CA 92078"},
]}}


def enrich(data, **extra):
    return enrich_leads([{"id": "parent", "url": SOURCE, "snippet": json.dumps(data), **extra}])[0]


def listing(**extra):
    return {"@type": "Product", "name": "123-129 Main St", **extra}


class StructuredPropertiesTests(unittest.TestCase):
    def assert_unknown(self, facts):
        self.assertEqual(set(facts), set(UNITS))
        for fact in facts.values():
            self.assertEqual(fact["status"], "unknown")
            self.assertIsNone(fact["value"])
            self.assertIsNone(fact["quote"])
            self.assertIsNone(fact["source_url"])

    def test_exact_screenshot_fixture_isolated_children(self):
        original = {"id": "old-job-lead", "url": SOURCE,
                    "title": "Strip Malls for Sale in San Diego County, CA",
                    "property_status": "Research source", "snippet": json.dumps(SCREENSHOT)}
        before = copy.deepcopy(original)
        result = enrich_leads([original])
        self.assertEqual(len(result), 1)
        parent = result[0]
        self.assertEqual(parent["id"], original["id"])
        self.assertEqual(parent["property_status"], "Research source")
        self.assertEqual(original, before)
        self.assert_unknown(parent["facts"])
        children = parent["properties"]
        self.assertEqual(len(children), 5)
        self.assertEqual(len({c["id"] for c in children}), 5)
        for child in children:
            self.assertEqual(set(child), {"id", "title", "url", "summary", "facts"})
            self.assertEqual(set(child["facts"]), set(UNITS))
            self.assertTrue(all(isinstance(child[k], str) for k in ("id", "title", "url", "summary")))
            self.assertNotIn("itemListElement", child["summary"])
            self.assertIsNone(child["facts"]["annual_noi"]["value"])
            for fact in child["facts"].values():
                if fact["status"] == "source_reported":
                    self.assertEqual(fact["source_url"], SOURCE)
                    self.assertIn(fact["quote"], original["snippet"])
        first, _, _, fourth, last = children
        for child, price, area, cap in [(first, 6000000, 20934, 7.25), (fourth, 1050000, 1610, None)]:
            self.assertEqual(child["facts"]["asking_price"]["value"], price)
            self.assertEqual(child["facts"]["area_sqft"]["value"], area)
            self.assertEqual(child["facts"]["cap_rate"]["value"], cap)
        for child in (children[1], children[2], last):
            self.assert_unknown(child["facts"])
        self.assertEqual(result, enrich_leads(result))

    def test_retained_fields_only_and_forged_children_removed(self):
        for field in ("snippet", "page_excerpt", "excerpts"):
            text = json.dumps(SCREENSHOT)
            parent = enrich_leads([{"url": SOURCE, field: [text] if field == "excerpts" else text}])[0]
            self.assertEqual(len(parent["properties"]), 5)
        result = enrich_leads([{"url": SOURCE, "summary": json.dumps(SCREENSHOT),
                                "properties": [{"facts": {"asking_price": 123}}]}])[0]
        self.assertNotIn("properties", result)
        self.assert_unknown(result["facts"])
        good = enrich(SCREENSHOT)
        good["properties"][0]["facts"]["asking_price"]["value"] = 1
        self.assertEqual(enrich_leads([good])[0]["properties"][0]["facts"]["asking_price"]["value"], 6000000)
        mixed = enrich(SCREENSHOT, page_excerpt="Asking price $99m")
        self.assert_unknown(mixed["facts"])
        self.assertEqual(mixed["properties"][0]["facts"]["asking_price"]["value"], 6000000)

    def test_single_typed_properties_and_list_variants(self):
        node = listing(description="20,934 SF Retail Building Offered at $6,000,000 at a 7.25% Cap Rate")
        for data in (node, {"mainEntity": node}, {**node, "@type": "RealEstateListing"},
                     {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "item": node}]},
                     {"@graph": [{"@type": "ItemList", "itemListElement": [{"item": {"@id": "#p"}}]},
                                 {**node, "@id": "#p"}]},
                     [{"@type": "ListItem", "item": node}]):
            with self.subTest(data=data):
                children = enrich(data)["properties"]
                self.assertEqual(len(children), 1)
                self.assertEqual(children[0]["facts"]["asking_price"]["value"], 6000000)
        for data in ({"mainEntity": {"name": "Organization", "description": "Asking price $1m"}},
                     {"mainEntity": {"@type": "Organization", "name": "Broker"}},
                     {"itemListElement": [{"@type": "Organization", "name": "Broker"}]}):
            self.assertNotIn("properties", enrich(data))

    def test_offer_currency_exact_quote_and_parent_provenance(self):
        offer = {"price": "5450000", "priceCurrency": "USD"}
        node = listing(offers=[offer], url="https://broker.example/listing/123")
        raw = json.dumps(node, indent=3)
        parent = enrich(node, snippet=raw)
        child = parent["properties"][0]
        price = child["facts"]["asking_price"]
        self.assertEqual(price["value"], 5450000)
        self.assertEqual(price["source_url"], SOURCE)
        self.assertEqual(child["url"], node["url"])
        self.assertIn(price["quote"], raw)
        self.assertEqual(json.loads(price["quote"]), offer)
        for bad in ({"price": "5450000"}, {"price": "5450000", "priceCurrency": "CAD"},
                    {"price": "5450000", "priceCurrency": "$"},
                    {"price": True, "priceCurrency": "USD"},
                    {"price": "5e6", "priceCurrency": "USD"},
                    {"price": -100, "priceCurrency": "USD"},
                    {"price": "9" * 100, "priceCurrency": "USD"},
                    {**offer, "highPrice": 6000000}, {**offer, "businessFunction": "LeaseOut"}):
            with self.subTest(offer=bad):
                self.assert_unknown(enrich(listing(offers=bad))["properties"][0]["facts"])

    def test_duplicate_identity_merges_conflicts_not_metrics_across_properties(self):
        a = listing(description="Offered at $2m; 15000SF", address="123 Main St", **{"@id": "reused"})
        b = {**a, "name": "456 Main St", "address": "456 Main St", "description": "Offered at $3m; 20000SF"}
        data = {"itemListElement": [a, a, b]}
        children = enrich(data)["properties"]
        self.assertEqual(len(children), 2)
        self.assertNotEqual(children[0]["id"], children[1]["id"])
        self.assertEqual(children[0]["facts"]["area_sqft"]["value"], 15000)
        conflict = enrich(data, page_excerpt=json.dumps({"itemListElement": [{**a, "description": "Offered at $3m; 20000SF"}]}))
        self.assertIsNone(conflict["properties"][0]["facts"]["asking_price"]["value"])
        self.assertIsNone(conflict["properties"][0]["facts"]["area_sqft"]["value"])
        reordered = enrich({"itemListElement": [b, a]})["properties"]
        self.assertEqual(children[0]["id"], reordered[1]["id"])
        self.assertNotEqual(children[0]["id"], enrich(data, id="other")["properties"][0]["id"])

    def test_offer_conflicts_projections_and_ranges(self):
        offer = {"price": 2000000, "priceCurrency": "USD"}
        for description in ("Offered at $3m", "Projected asking price $2m", "Offered at $2m-$3m",
                            "Offered at $2m; Previously offered at $3m"):
            child = enrich(listing(description=description, offers=offer))["properties"][0]
            self.assertIsNone(child["facts"]["asking_price"]["value"])
        for offers in ([offer, {**offer, "price": 3000000}], [offer, {"price": 2000000}],
                       [{**offer, "description": "Projected"}]):
            self.assert_unknown(enrich(listing(offers=offers))["properties"][0]["facts"])
        child = enrich(listing(description="Projected cap rate 7%; 15000-20000SF"))["properties"][0]
        self.assert_unknown(child["facts"])

    def test_generic_names_never_merge_distinct_records(self):
        a = {"@type": "RetailBuilding", "name": "Retail building", "description": "15000SF"}
        b = {**a, "description": "Offered at $3m"}
        for identifiers in (({}, {}), ({"@id": "#a"}, {"@id": "#b"}),
                            ({"address": "123 Main St"}, {"address": "456 Main St"}),
                            ({"@id": "#same", "address": {"streetAddress": "123 Main St"}},
                             {"@id": "#same", "address": {"streetAddress": "456 Main St"}})):
            with self.subTest(identifiers=identifiers):
                data = {"itemListElement": [{**a, **identifiers[0]}, {**b, **identifiers[1]}]}
                parent = enrich(data)
                children = parent["properties"]
                self.assertEqual(len(children), 2)
                self.assertNotEqual(children[0]["id"], children[1]["id"])
                self.assertEqual(children[0]["facts"]["area_sqft"]["value"], 15000)
                self.assertIsNone(children[0]["facts"]["asking_price"]["value"])
                self.assertEqual(children[1]["facts"]["asking_price"]["value"], 3000000)
                self.assertIsNone(children[1]["facts"]["area_sqft"]["value"])
                self.assertEqual(parent, enrich_leads([parent])[0])
                duplicate = json.dumps(data, sort_keys=True, indent=2)
                self.assertEqual(children, enrich(data, page_excerpt=duplicate)["properties"])

    def test_explicit_identity_conflicts_survive_changed_name(self):
        for identity in ({"@id": "#a"}, {"address": {"streetAddress": "123 Main St"}},
                         {"@id": "#a", "address": "123 Main St"}):
            a = listing(**identity, description="15000SF; Offered at $2m")
            b = {**a, "name": "Updated listing title", "description": "20000SF; Offered at $3m"}
            parent = enrich(a, page_excerpt=json.dumps(b))
            self.assertEqual(len(parent["properties"]), 1)
            self.assert_unknown(parent["properties"][0]["facts"])
            self.assertEqual(enrich(a)["properties"][0]["id"], enrich(b)["properties"][0]["id"])

    def test_rental_offers_use_enclosing_record_and_price_units(self):
        offer = {"price": 2500, "priceCurrency": "USD"}
        for context in ({"description": "For lease: $2500 per month"},
                        {"description": "$2,500 monthly"},
                        {"name": "Retail space for rent"},
                        {"businessFunction": "http://purl.org/goodrelations/v1#LeaseOut"},
                        {"businessFunction": "Rent"}, {"priceUnit": "MONTH"},
                        {"unitText": "per month"}):
            with self.subTest(context=context):
                self.assert_unknown(enrich(listing(offers=offer, **context))["properties"][0]["facts"])
        for context in ({"priceUnit": "MONTH"}, {"businessFunction": "RentOut"},
                        {"unitCode": "MON"}):
            self.assert_unknown(enrich(listing(offers={**offer, **context}))["properties"][0]["facts"])
        wrapper = {"itemListElement": [{"@type": "ListItem", "description": "For lease",
                                         "item": listing(offers=offer)}]}
        self.assert_unknown(enrich(wrapper)["properties"][0]["facts"])

    def test_sale_mentions_of_rent_or_leases_are_not_rental_offers(self):
        for description in ("Fully leased retail building Offered at $2m",
                            "Retail building Offered at $2m; annual rent income $120000",
                            "Offered at $2m with long-term leases",
                            "Offered at $2m; Asking rent $2500/month",
                            "Offered at $2m with rent roll available"):
            child = enrich(listing(description=description,
                                   offers={"price": 2000000, "priceCurrency": "USD"}))["properties"][0]
            self.assertEqual(child["facts"]["asking_price"]["value"], 2000000, description)

    def test_escaped_description_quotes_are_exact_retained_json(self):
        raw = (r'{"mainEntity":{"@type":"Product","name":"Retail building",'
               r'"description":"Caf\u00e9 \"special\": Offered at \u00242m; '
               r'15000SF; cap rate 6%; 90% occupied; annual NOI \u0024120000; '
               r'Asking rent \u002424/SF/year; Population 50000; '
               r'Median household income \u002485000"}}')
        data = json.loads(raw)
        parent = enrich(data, snippet=raw)
        child = parent["properties"][0]
        self.assertNotIn("\\u0024", child["summary"])
        for key, fact in child["facts"].items():
            self.assertEqual(fact["status"], "source_reported", key)
            self.assertIn(fact["quote"], raw)
            self.assertEqual(json.loads(fact["quote"]), data["mainEntity"])
            self.assertEqual(fact["source_url"], SOURCE)
        self.assertEqual(parent, enrich_leads([parent])[0])

    def test_bounds_malformed_json_and_graph_cycles_fail_closed(self):
        good = json.dumps(SCREENSHOT)
        bad_json = (good[:-1], '{"mainEntity":{},"mainEntity":' + good + '}',
                    '{"itemListElement":[],"x":NaN}', "[" * 1000 + "]" * 1000)
        for text in bad_json:
            parent = enrich(SCREENSHOT, page_excerpt=text)
            self.assertNotIn("properties", parent)
            self.assert_unknown(parent["facts"])
        for extras in ({"excerpts": [good] * (MAX_EXCERPTS + 1)}, {"page_excerpt": "x" * (MAX_TEXT + 1)}):
            self.assertNotIn("properties", enrich(SCREENSHOT, **extras))
        self.assertNotIn("properties", enrich({"itemListElement": [listing()] * (MAX_PROPERTIES + 1)}))
        self.assertNotIn("properties", enrich({"@graph": [{"@id": "#x", "mainEntity": {"@id": "#x"}}]}))
        self.assertNotIn("properties", enrich({"@graph": [{"@id": "#x"}, {"@id": "#x"}]}))

    def test_unsafe_urls(self):
        for url in ("javascript:alert(1)", "http://127.1", "https://localhost/x", "/relative"):
            self.assertNotIn("properties", enrich(SCREENSHOT, url=url))
            child = enrich(listing(url=url, description="Offered at $2m"))["properties"][0]
            self.assertEqual(child["url"], SOURCE)
            self.assertEqual(child["facts"]["asking_price"]["source_url"], SOURCE)


if __name__ == "__main__":
    unittest.main()
