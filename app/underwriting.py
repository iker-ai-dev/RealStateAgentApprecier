"""Deterministic annual cash-flow scenarios from explicit user assumptions."""
import math

import numpy_financial as npf
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    purchase_price: float = Field(ge=1, le=1e10)
    annual_noi: float = Field(ge=0, le=1e9)
    hold_years: int = Field(default=7, ge=1, le=30)
    noi_growth: float = Field(default=0, ge=-0.5, le=0.5)
    exit_cap_rate: float = Field(ge=0.001, le=0.5)
    acquisition_cost_pct: float = Field(default=0, ge=0, le=0.2)
    disposition_cost_pct: float = Field(default=0, ge=0, le=0.2)
    initial_capex: float = Field(default=0, ge=0, le=1e9)
    annual_capex: float = Field(default=0, ge=0, le=1e9)
    loan_to_value: float = Field(default=0, ge=0, lt=1)
    interest_rate: float = Field(default=0, ge=0, le=0.5)
    amortization_years: int = Field(default=30, ge=1, le=50)
    interest_only: bool = False

    @model_validator(mode="after")
    def loan_term(self):
        if self.loan_to_value and not self.interest_only and self.hold_years > self.amortization_years:
            raise ValueError("Hold period cannot exceed amortization period in this scenario.")
        return self


def calculate_scenario(scenario: Scenario) -> dict:
    debt = scenario.purchase_price * scenario.loan_to_value
    equity = scenario.purchase_price * (1 + scenario.acquisition_cost_pct) + scenario.initial_capex - debt
    months = scenario.amortization_years * 12
    rate = scenario.interest_rate / 12
    if scenario.interest_only:
        payment = debt * rate
    elif rate == 0:
        payment = debt / months
    elif rate < 1e-8:
        # Avoid cancellation in (1 + rate)**months - 1 near the zero-rate limit.
        payment = debt * rate / -math.expm1(-months * math.log1p(rate))
    else:
        payment = float(-npf.pmt(rate, months, debt))
    if not math.isfinite(payment):
        raise ValueError("The debt payment cannot be calculated from these assumptions.")
    balance = debt
    cashflows = [-equity]
    years = []
    for year in range(1, scenario.hold_years + 1):
        noi = scenario.annual_noi * (1 + scenario.noi_growth) ** (year - 1)
        debt_service = 0.0
        for _ in range(12):
            monthly_payment = min(payment, balance * (1 + rate))
            debt_service += monthly_payment
            balance = max(0.0, balance * (1 + rate) - monthly_payment)
        operating_cash = noi - scenario.annual_capex - debt_service
        exit_value = 0.0
        sale_proceeds = 0.0
        if year == scenario.hold_years:
            exit_value = noi * (1 + scenario.noi_growth) / scenario.exit_cap_rate
            sale_proceeds = exit_value * (1 - scenario.disposition_cost_pct) - balance
        cash = operating_cash + sale_proceeds
        cashflows.append(cash)
        years.append({"year": year, "noi": noi, "debt_service": debt_service,
                      "capex": scenario.annual_capex, "operating_cash_flow": operating_cash,
                      "debt_balance": balance, "exit_value": exit_value,
                      "net_sale_proceeds": sale_proceeds, "equity_cash_flow": cash})
    contributions = -sum(min(cash, 0) for cash in cashflows)
    distributions = sum(max(cash, 0) for cash in cashflows)
    signs = [1 if cash > 0 else -1 for cash in cashflows if cash]
    multiple_roots_possible = sum(a != b for a, b in zip(signs, signs[1:])) > 1
    irr = float(npf.irr(cashflows)) if not multiple_roots_possible else math.nan
    return {"assumptions": scenario.model_dump(), "initial_equity": equity,
            "annual_cash_flows": years, "equity_cash_flows": cashflows,
            "irr": irr if math.isfinite(irr) else None,
            "irr_status": "available" if math.isfinite(irr) else "not_unique_or_undefined",
            "equity_multiple": distributions / contributions,
            "total_equity_contributed": contributions, "total_distributions": distributions,
            "entry_cap_rate": scenario.annual_noi / scenario.purchase_price,
            "basis": "User-supplied assumptions; annual end-of-year cash flows, monthly debt amortization, before tax.",
            "exclusions": ["Income taxes", "Refinancing", "Lease-by-lease rollover", "Intra-year equity timing"]}
