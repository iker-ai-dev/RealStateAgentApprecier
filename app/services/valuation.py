from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd
from app.acquisition_policy import GATE, validate_model_inputs, render_research_answer
from app.services.topic_router import local_topic


@dataclass(frozen=True)
class ValuationResult:
    property_id: str
    noi: float
    stabilized_noi: float
    market_rent_psf: float
    current_rent_psf: float
    occupancy: float
    cap_rate: float
    cap_rate_value: float
    sales_comp_value: float
    dcf_value: float
    blended_value: float
    value_psf: float
    asking_price: float
    equity_spread: float
    recommendation: str
    risks: list[str]
    opportunities: list[str]

    def __post_init__(self):
        for value in self.__dict__.values():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError("Illustrative model produced a nonfinite value; correct inputs.")



def build_valuation(
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
) -> ValuationResult:
    validate_model_inputs(property_row, tenants, market, comps)
    gla = float(property_row["gla_sf"])
    occupied = tenants[tenants["category"].str.lower() != "vacant"].copy()

    gross_rent = float((occupied["area_sf"] * occupied["rent_psf"]).sum())
    reimbursements = gross_rent * 0.22
    vacancy_loss = 0.0  # Physical vacancy is already excluded from occupied lease rent.
    operating_expenses = gross_rent * 0.31
    noi = gross_rent + reimbursements - vacancy_loss - operating_expenses

    current_rent_psf = _weighted_average(occupied, "rent_psf", "area_sf")
    market_rent_psf = _estimate_market_rent_psf(current_rent_psf, market, comps)
    vacant_area = float(tenants.loc[tenants["category"].str.lower() == "vacant", "area_sf"].sum())
    mark_to_market = max(0, market_rent_psf - current_rent_psf) * float(occupied["area_sf"].sum())
    lease_up = vacant_area * market_rent_psf * 0.88
    stabilized_noi = noi + (mark_to_market * 0.62) + (lease_up * 0.62)

    cap_rate = _risk_adjusted_cap_rate(property_row, tenants, market, comps)
    cap_rate_value = stabilized_noi / cap_rate
    sales_comp_value = float(comps["price_psf"].median()) * gla
    dcf_value = _discounted_cash_flow(stabilized_noi, cap_rate)
    blended_value = (cap_rate_value * 0.5) + (sales_comp_value * 0.25) + (dcf_value * 0.25)
    value_psf = blended_value / gla
    asking_price = float(property_row.get("asking_price", 0) or 0)
    equity_spread = (blended_value - asking_price) / asking_price if asking_price else 0

    risks = _identify_risks(property_row, tenants, market, cap_rate)
    opportunities = _identify_opportunities(current_rent_psf, market_rent_psf, tenants, market)

    recommendation = GATE

    return ValuationResult(
        property_id=str(property_row["property_id"]),
        noi=noi,
        stabilized_noi=stabilized_noi,
        market_rent_psf=market_rent_psf,
        current_rent_psf=current_rent_psf,
        occupancy=float(property_row["occupancy_pct"]) / 100,
        cap_rate=cap_rate,
        cap_rate_value=cap_rate_value,
        sales_comp_value=sales_comp_value,
        dcf_value=dcf_value,
        blended_value=blended_value,
        value_psf=value_psf,
        asking_price=asking_price,
        equity_spread=equity_spread,
        recommendation=recommendation,
        risks=risks,
        opportunities=opportunities,
    )


def answer_locally(
    question: str,
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
) -> str:
    return render_research_answer(local_topic(question), property_row, tenants, market, comps, valuation)


def _estimate_market_rent_psf(current_rent_psf: float, market: pd.Series, comps: pd.DataFrame) -> float:
    income_factor = min(1.18, max(0.92, float(market["median_income"]) / 95000))
    growth_factor = 1 + (float(market["growth_5yr_pct"]) / 100 * 0.45)
    comp_factor = min(1.12, max(0.9, float(comps["price_psf"].median()) / 280))
    return current_rent_psf * income_factor * growth_factor * comp_factor


def _risk_adjusted_cap_rate(
    property_row: pd.Series, tenants: pd.DataFrame, market: pd.Series, comps: pd.DataFrame
) -> float:
    base = float(comps["cap_rate"].median())
    occupancy_penalty = max(0, 0.94 - float(property_row["occupancy_pct"]) / 100) * 0.045
    income_credit = max(0, (float(market["median_income"]) - 95000) / 100000) * 0.004
    rollover_penalty = _near_term_rollover_pct(tenants) * 0.012
    return min(0.0825, max(0.0475, base + occupancy_penalty + rollover_penalty - income_credit))


def _discounted_cash_flow(stabilized_noi: float, cap_rate: float) -> float:
    discount_rate = cap_rate + 0.021
    growth = 0.0275
    cash_flows = []
    noi = stabilized_noi
    for year in range(1, 11):
        noi *= 1 + growth
        cash_flows.append(noi / ((1 + discount_rate) ** year))
    terminal_value = (noi * 1.02) / max(0.045, cap_rate + 0.0025)
    return sum(cash_flows) + terminal_value / ((1 + discount_rate) ** 10)


def _identify_risks(
    property_row: pd.Series, tenants: pd.DataFrame, market: pd.Series, cap_rate: float
) -> list[str]:
    risks = []
    if float(property_row["occupancy_pct"]) < 92:
        risks.append("occupancy below institutional target")
    if _near_term_rollover_pct(tenants) > 0.18:
        risks.append("meaningful near-term lease rollover")
    if float(market["median_income"]) < 85000:
        risks.append("trade-area income below preferred threshold")
    if cap_rate > 0.0675:
        risks.append("market requires a wider risk premium")
    unknown = pd.to_datetime(tenants.loc[tenants["category"].str.lower() != "vacant", "lease_end"], errors="coerce").isna().any()
    if unknown:
        risks.append("lease expiration evidence is incomplete; rollover exposure remains unknown")
    return risks + ["unverified inputs and incomplete financial diligence prevent a risk-clearance conclusion"]


def _identify_opportunities(
    current_rent_psf: float, market_rent_psf: float, tenants: pd.DataFrame, market: pd.Series
) -> list[str]:
    opportunities = []
    if market_rent_psf > current_rent_psf * 1.05:
        opportunities.append("test the illustrative rent gap against executed lease comps and lease terms; below-market rent is not established")
    if tenants["category"].str.lower().eq("vacant").any():
        opportunities.append("investigate vacant-suite demand, TI/LC and carry before assuming lease-up")
    if float(market["growth_5yr_pct"]) > 7:
        opportunities.append("verify dated population trends and local retail demand; rent growth is not established")
    return opportunities or ["no supported value-add thesis established; obtain operating and leasing evidence"]


def _near_term_rollover_pct(tenants: pd.DataFrame) -> float:
    occupied = tenants[tenants["category"].str.lower() != "vacant"].copy()
    if occupied.empty:
        return 0
    lease_end = pd.to_datetime(occupied["lease_end"], errors="coerce")
    cutoff = pd.Timestamp(date.today()).normalize() + pd.DateOffset(years=3)
    rolling_area = float(occupied.loc[lease_end <= cutoff, "area_sf"].sum())
    return rolling_area / float(occupied["area_sf"].sum())


def _weighted_average(df: pd.DataFrame, value_col: str, weight_col: str) -> float:
    weights = df[weight_col].astype(float)
    if weights.sum() == 0:
        return 0
    return float((df[value_col].astype(float) * weights).sum() / weights.sum())


def _money(value: float) -> str:
    return f"${value:,.0f}"
