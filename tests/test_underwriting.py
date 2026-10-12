import unittest

from pydantic import ValidationError

from app.underwriting import Scenario, calculate_scenario


class UnderwritingTests(unittest.TestCase):
    def test_unlevered_flat_income_return(self):
        result = calculate_scenario(Scenario(purchase_price=1_000_000, annual_noi=100_000,
                                            exit_cap_rate=0.1, hold_years=5))
        self.assertAlmostEqual(result["irr"], 0.1)
        self.assertAlmostEqual(result["equity_multiple"], 1.5)
        self.assertEqual(result["annual_cash_flows"][-1]["exit_value"], 1_000_000)

    def test_interest_only_and_amortization(self):
        inputs = dict(purchase_price=1_000_000, annual_noi=100_000, exit_cap_rate=0.1,
                      loan_to_value=0.6, interest_rate=0.06, hold_years=5)
        interest_only = calculate_scenario(Scenario(**inputs, interest_only=True))
        amortizing = calculate_scenario(Scenario(**inputs))
        self.assertAlmostEqual(interest_only["annual_cash_flows"][0]["debt_service"], 36_000)
        self.assertAlmostEqual(interest_only["annual_cash_flows"][-1]["debt_balance"], 600_000)
        self.assertLess(amortizing["annual_cash_flows"][-1]["debt_balance"], 600_000)

    def test_later_losses_count_as_contributions(self):
        result = calculate_scenario(Scenario(purchase_price=1_000_000, annual_noi=50_000,
                                            annual_capex=100_000, exit_cap_rate=0.05, hold_years=5))
        self.assertEqual(result["total_equity_contributed"], 1_200_000)
        self.assertAlmostEqual(result["equity_multiple"], 950_000 / 1_200_000)

    def test_near_zero_interest_does_not_accelerate_debt_payoff(self):
        result = calculate_scenario(Scenario(purchase_price=1000000, annual_noi=100000,
            exit_cap_rate=0.1, loan_to_value=0.6, interest_rate=1e-16))
        self.assertAlmostEqual(result["annual_cash_flows"][0]["debt_service"], 20000, places=6)
        self.assertAlmostEqual(result["annual_cash_flows"][0]["debt_balance"], 580000, places=6)

    def test_rejects_infinite_and_invalid_assumptions(self):
        for price in [float("inf"), float("nan"), 0, -1]:
            with self.assertRaises(ValidationError):
                Scenario(purchase_price=price, annual_noi=0, exit_cap_rate=0.05)
        with self.assertRaises(ValidationError):
            Scenario(purchase_price=1000000, annual_noi=100000, exit_cap_rate=1e-300)
