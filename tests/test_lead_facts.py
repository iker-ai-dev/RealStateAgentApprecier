import copy
import json
import unittest

from app.lead_facts import MAX_EXCERPTS, MAX_TEXT, UNITS, enrich_leads


URL = "https://broker.example/listing/oak-plaza"


def lead(text="", **kwargs):
    return {"url": URL, "snippet": text, **kwargs}


def facts(text="", **kwargs):
    return enrich_leads([lead(text, **kwargs)])[0]["facts"]


class LeadFactsTests(unittest.TestCase):
    def assert_unknown(self, fact):
        self.assertEqual(fact["status"], "unknown")
        for key in ("value", "quote", "source_url"):
            self.assertIsNone(fact[key], key)
        self.assertIsInstance(fact["unit"], str)

    def test_single_property_all_metrics_and_exact_citations(self):
        text = ("Oak Plaza. Asking price: $2m; Building size: 15000SF; 6%cap; "
                "90%occupied; annualNOI: $120,000; Asking rent: $24/SF/year; "
                "Within 3 miles, population: 50,000; Median household income: $85,000")
        expected = {"asking_price": 2000000, "area_sqft": 15000, "cap_rate": 6,
                    "occupancy_pct": 90, "annual_noi": 120000, "asking_rent_psf": 24,
                    "population": 50000, "median_household_income": 85000}
        result = facts(text)
        self.assertEqual(set(result), set(expected))
        for key, value in expected.items():
            with self.subTest(key=key):
                fact = result[key]
                self.assertEqual(fact["value"], value)
                self.assertEqual(fact["status"], "source_reported")
                self.assertIn(fact["quote"], text)
                self.assertEqual(fact["source_url"], URL)
                self.assertEqual(set(fact), {"value", "unit", "quote", "source_url", "status"})
        self.assertEqual(result["asking_rent_psf"]["unit"], "USD/sqft/year")
        self.assertIn("Within 3 miles", result["population"]["quote"])

    def test_numeric_notation_and_label_variants(self):
        cases = [
            ("Asking price: $2 million", "asking_price", 2000000),
            ("$2.25M asking price", "asking_price", 2250000),
            ("Listing price USD 2,500,000", "asking_price", 2500000),
            ("Asking price: $2,000,000, cap rate: 6.25%", "asking_price", 2000000),
            ("Building: 15,000 square feet", "area_sqft", 15000),
            ("GLA: 15,000 sq. ft.", "area_sqft", 15000),
            ("Capitalization rate: 6.25%", "cap_rate", 6.25),
            ("Occupancy rate: 90%", "occupancy_pct", 90),
            ("95% leased", "occupancy_pct", 95),
            ("Annual net operating income: $125.5 thousand", "annual_noi", 125500),
            ("NOI: $120,000/year", "annual_noi", 120000),
            ("Net operating income: USD 120000 annually", "annual_noi", 120000),
            ("Population: 1.5 million", "population", 1500000),
        ]
        for text, key, value in cases:
            with self.subTest(text=text):
                self.assertEqual(facts(text)[key]["value"], value)

    def test_broker_flattened_markdown_fact_table(self):
        text = ("# Shops on Thomas 3404 E Thomas Rd, Phoenix, AZ 85018 "
                "#### Listing Price: $3,400,000 Offering Memorandum & Deal Room "
                "Cap Rate 1.30% Gross SF 21,930 Rentable SF 21,930 Price/Gross SF $155.04 "
                "Lot Size 1.44 acres Year Built 1960 ## Investment Overview "
                "Rents have potential upside and are approximately $8 per square foot.")
        result = facts(text)
        self.assertEqual(result['asking_price']['value'], 3400000)
        self.assertEqual(result['cap_rate']['value'], 1.3)
        self.assertEqual(result['area_sqft']['value'], 21930)
        self.assertIn(result['area_sqft']['quote'], text)
        self.assert_unknown(facts('Price/Gross SF $155.04')['area_sqft'])
        self.assert_unknown(facts('Gross SF 21,930 Rentable SF 20,000')['area_sqft'])

    def test_broker_multiline_table_and_unrelated_rent_qualifier(self):
        text = ('Cap Rate\n\n1.30%\n\nGross SF\n\n21,930\n\nRentable SF\n\n21,930\n\n'
                'The center is 86% occupied with 2 vacant suites and features rents '
                'averaging approximately $8 per square foot.')
        result = facts(text)
        self.assertEqual(result['cap_rate']['value'], 1.3)
        self.assertEqual(result['area_sqft']['value'], 21930)
        self.assertEqual(result['occupancy_pct']['value'], 86)
        self.assertIn(result['cap_rate']['quote'], text)
        self.assert_unknown(facts('Projected\nCap Rate\n6%')['cap_rate'])
        self.assert_unknown(facts('Gross SF\n21930\nRentable SF\n20000')['area_sqft'])

    def test_empty_slots_and_missing_input_fields(self):
        self.assertEqual(enrich_leads([]), [])
        for item in ({}, {"url": URL}, lead("  "), lead(None), lead(excerpts=[])):
            with self.subTest(item=item):
                result = enrich_leads([item])[0]["facts"]
                self.assertEqual(set(result), set(UNITS))
                for key, fact in result.items():
                    self.assert_unknown(fact)
                    self.assertEqual(fact["unit"], UNITS[key])

    def test_inputs_are_deep_copied_and_old_facts_are_replaced(self):
        original = [lead("Asking price: $2m", excerpts=["90% occupied"],
                         facts={"asking_price": {"value": 99}}, metadata={"tags": ["retail"]})]
        before = copy.deepcopy(original)
        result = enrich_leads(original)
        self.assertEqual(original, before)
        self.assertEqual(result, enrich_leads(original))
        self.assertEqual(result, enrich_leads(result))
        result[0]["metadata"]["tags"].append("changed")
        result[0]["excerpts"].append("changed")
        self.assertEqual(original, before)

    def test_no_numeric_field_title_query_or_summary_inference(self):
        result = facts("Broker says call for details", title="Asking price $2m; 90% occupied",
                       query="Annual NOI $100k", summary="Cap rate 6%", asking_price=2000000,
                       area_sqft=15000, population=50000)
        for fact in result.values():
            self.assert_unknown(fact)

    def test_no_unlabelled_financial_numbers_or_calculation(self):
        result = facts("$2 million; NOI $120000; 10% vacancy; 100 households; "
                       "Average household income $95000; Rent $24/SF; Price per acre $1000")
        for fact in result.values():
            self.assert_unknown(fact)
        result = facts("Asking price $2m; Cap rate 6%")
        self.assert_unknown(result["annual_noi"])

    def test_ranges_qualifiers_and_malformed_numbers_are_unknown(self):
        cases = ["$2m-$3m", "$2 to $3 million", "$2m / $3m", "$2m or $3m",
                 "$2m+", "$2m - 3m", "approximately $2m", "over $2m", "-$2m",
                 "$2,00,000", "$2.3.4m", "$2e6", "$2mm", "$2m CAD", "$2m, $3m",
                 "$200/SF", "$200 per square foot"]
        for value in cases:
            with self.subTest(value=value):
                self.assert_unknown(facts("Asking price: " + value)["asking_price"])
        for text, key in [("Cap rate: 6-7%", "cap_rate"),
                          ("6% to 7% cap rate", "cap_rate"),
                          ("90-95% occupied", "occupancy_pct"),
                          ("15,000-20,000 SF", "area_sqft"),
                          ("Population: 50000-60000", "population"),
                          ("Projected annual NOI $120000", "annual_noi"),
                          ("Annual NOI $120000/month", "annual_noi"),
                          ("Pro forma occupancy: 95%", "occupancy_pct")]:
            with self.subTest(text=text):
                self.assert_unknown(facts(text)[key])

    def test_ambiguous_and_conflicting_snippets_do_not_choose_winner(self):
        for evidence in [
            {"page_excerpt": "Asking price $3m"},
            {"excerpts": ["Asking price $3m"]},
            {"page_excerpt": "Asking price $2m-$3m"},
            {"page_excerpt": "Projected asking price $3m"},
        ]:
            with self.subTest(evidence=evidence):
                self.assert_unknown(facts("Asking price $2m", **evidence)["asking_price"])
        self.assert_unknown(facts("Asking price $2m; Asking price $3m")["asking_price"])

    def test_duplicate_evidence_and_equivalent_notation_are_not_conflicts(self):
        result = facts("Asking price $2m", page_excerpt="Asking price $2,000,000",
                       excerpts=["Asking price $2m", "Asking price $2,000,000"])
        self.assertEqual(result["asking_price"]["value"], 2000000)
        self.assertEqual(result["asking_price"]["quote"], "Asking price $2m")

    def test_excerpts_are_separate_and_do_not_complete_each_others_labels(self):
        self.assert_unknown(facts("Asking price:", page_excerpt="$2m")["asking_price"])
        result = facts(excerpts=["Asking price $2m", "Annual NOI $120000"])
        self.assertEqual(result["asking_price"]["value"], 2000000)
        self.assertEqual(result["annual_noi"]["value"], 120000)

    def test_catalog_and_multiple_properties_never_supply_property_facts(self):
        numeric = "Asking price $2m; 15000SF; 6%cap; 90%occupied; Annual NOI $120000; Asking rent $24/SF"
        cases = [
            lead(numeric, lead_kind="listing_directory"),
            lead(numeric, lead_kind="market_report"),
            lead(numeric, url="https://www.loopnet.com/search/retail/tx/for-sale/"),
            lead(numeric, url="https://www.crexi.com/properties/TX/Shopping-Centers"),
            lead(numeric, url="https://broker.example/listings"),
            lead(numeric, url="https://broker.example/listings/phoenix"),
            lead(numeric, url="https://broker.example/catalog/retail"),
            lead(numeric, url="https://broker.example/index.html"),
            lead(numeric, source_kind="index"),
            lead(numeric, source_kind="listing_index"),
            lead(numeric, title="Phoenix, AZ Shopping Centers for Sale"),
            lead(numeric, title="Phoenix Strip Malls for Sale"),
            lead(numeric, title="Retail properties for sale"),
            lead("12 listings. " + numeric),
            lead(numeric, page_excerpt="Similar properties for sale"),
            lead("Oak Plaza: Asking price $2m; Pine Center: 15000SF"),
            lead("123 Main St: Asking price $2m; 456 First Avenue: 15000SF"),
            lead("Property A: Asking price $2m", page_excerpt="Property B: 15000SF"),
            lead("Asking price $2m; https://broker.example/listing/a; "
                 "15000SF; https://broker.example/listing/b"),
            lead("Portfolio: " + numeric),
        ]
        for item in cases:
            with self.subTest(item=item):
                result = enrich_leads([item])[0]["facts"]
                for key in set(UNITS) - {"population", "median_household_income"}:
                    self.assert_unknown(result[key])

    def test_single_shopping_center_title_is_not_a_catalog(self):
        result = facts("Asking price $2m; 15000SF", title="Oak Shopping Center for Sale")
        self.assertEqual(result["asking_price"]["value"], 2000000)
        self.assertEqual(result["area_sqft"]["value"], 15000)

    def test_demographic_context_is_preserved_without_inventing_radius(self):
        text = "City of Example population: 50,000"
        result = facts(text, lead_kind="market_report")
        self.assertEqual(result["population"]["value"], 50000)
        self.assertEqual(result["population"]["quote"], text)
        self.assertNotIn("radius", json.dumps(result))
        self.assertEqual(facts("Population: 50000")["population"]["value"], 50000)
        self.assert_unknown(facts("Population: 2020: 50000")["population"])

    def test_multiple_demographic_geographies_or_years_are_ambiguous(self):
        for text in ["1 mile population: 10000; 3 mile population: 50000",
                     "City population: 50000; County population: 50000",
                     "1 mile population: 50000, 3 mile population: 50000",
                     "2020 population: 50000; 2025 population: 60000"]:
            with self.subTest(text=text):
                self.assert_unknown(facts(text)["population"])
        self.assert_unknown(facts("1 mile median household income: $70000; "
                                  "3 mile median household income: $90000")["median_household_income"])

    def test_rent_period_is_not_assumed_or_converted(self):
        for suffix, unit in [("", "USD/sqft"), ("/yr", "USD/sqft/year"),
                             ("/month", "USD/sqft/month")]:
            with self.subTest(suffix=suffix):
                fact = facts("Asking rent: $2.50/SF" + suffix)["asking_rent_psf"]
                self.assertEqual(fact["value"], 2.5)
                self.assertEqual(fact["unit"], unit)
        self.assert_unknown(facts("Asking rent $24/SF/year",
                                  page_excerpt="Asking rent $24/SF/month")["asking_rent_psf"])

    def test_invalid_percentage_and_fractional_population(self):
        for text, key in [("Occupancy 101%", "occupancy_pct"), ("Cap rate 110%", "cap_rate"),
                          ("Population 10.5", "population"), ("Occupancy -5%", "occupancy_pct")]:
            self.assert_unknown(facts(text)[key])
        self.assertEqual(facts("0% occupied")["occupancy_pct"]["value"], 0)
        self.assertEqual(facts("100% occupied")["occupancy_pct"]["value"], 100)

    def test_no_suite_lot_or_available_area_as_building_area(self):
        for text in ["Lot area 15000SF", "Suite size 15000SF", "15000SF available",
                     "Divisible 15000SF", "Land: 15000SF"]:
            self.assert_unknown(facts(text)["area_sqft"])

    def test_unsafe_or_absent_source_urls_never_get_citations(self):
        urls = [None, "", "javascript:alert(1)", "file:///tmp/source", "/relative",
                "http://127.0.0.1/x", "http://[::1]/x", "http://10.0.0.1/x",
                "https://localhost/x", "https://source.internal/x", "https://example.com:8080/x",
                "https://user:password@example.com/x", "https://example.com/x?api_key=secret",
                "https://example.com/\npath", "https://example.com\\@localhost/x", "https://[bad"]
        urls += ["http://localhost./x", "http://127.1/x", "http://0x7f.0.0.1/x",
                 "http://127.0.0.1./x", "http://source.internal./x", "http://foo.localhost/x"]
        for url in urls:
            with self.subTest(url=url):
                self.assert_unknown(facts("Asking price $2m", url=url)["asking_price"])

    def test_source_url_alias_is_supported_without_arbitrary_url_fallback(self):
        item = {"source_url": URL, "page_excerpt": "Asking price $2m"}
        fact = enrich_leads([item])[0]["facts"]["asking_price"]
        self.assertEqual(fact["source_url"], URL)
        self.assert_unknown(facts("Asking price $2m", source_url="https://other.example/x")["asking_price"])

    def test_bounds_do_not_truncate_into_facts_or_hide_conflicts(self):
        for evidence in [
            {"page_excerpt": "x" * MAX_TEXT + " Asking price $3m"},
            {"excerpts": ["Asking price $2m"] * (MAX_EXCERPTS + 1)},
            {"excerpts": [{"text": "Asking price $3m", "url": "https://other.example/x"}]},
            {"excerpts": "Asking price $3m"},
            {"page_excerpt": 3000000},
        ]:
            with self.subTest(evidence=str(evidence)[:100]):
                self.assert_unknown(facts("Asking price $2m", **evidence)["asking_price"])
        self.assert_unknown(facts("Asking price $" + "9" * 200)["asking_price"])

    def test_leads_do_not_share_candidates(self):
        result = enrich_leads([lead("Asking price $2m"), lead("Annual NOI $120000")])
        self.assert_unknown(result[0]["facts"]["annual_noi"])
        self.assert_unknown(result[1]["facts"]["asking_price"])


if __name__ == "__main__":
    unittest.main()
