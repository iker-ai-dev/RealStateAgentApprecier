from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

import pandas as pd
import streamlit as st

try:
    import sentry_sdk
except Exception:  # pragma: no cover - optional Snowflake package
    sentry_sdk = None


PROPERTY_ROWS = [
    {
        "property_id": "SC-001",
        "name": "Westgate Plaza",
        "address": "1840 W Main Street",
        "city": "Phoenix",
        "state": "AZ",
        "gla_sf": 84200,
        "year_built": 2007,
        "occupancy_pct": 94,
        "asking_price": 18800000,
        "notes": "Grocery-anchored open-air strip center with pad exposure and recent facade refresh.",
    },
    {
        "property_id": "SC-002",
        "name": "Oak Ridge Commons",
        "address": "6104 Oak Ridge Road",
        "city": "Charlotte",
        "state": "NC",
        "gla_sf": 118500,
        "year_built": 2012,
        "occupancy_pct": 89,
        "asking_price": 27600000,
        "notes": "Daily-needs center near a growing suburban corridor with below-market junior anchor rent.",
    },
    {
        "property_id": "SC-003",
        "name": "Sunset Marketplace",
        "address": "9200 Sunset Boulevard",
        "city": "Austin",
        "state": "TX",
        "gla_sf": 67500,
        "year_built": 2018,
        "occupancy_pct": 97,
        "asking_price": 23100000,
        "notes": "High-income infill strip with service tenants, strong traffic, and limited near-term rollover.",
    },
]

TENANT_ROWS = [
    ("SC-001", "Fresh Basket", "Grocery", "100", 28600, 18.75, "2031-06-30", "National", 515),
    ("SC-001", "Anytime Fitness", "Fitness", "140", 8500, 22.5, "2029-12-31", "Regional", 310),
    ("SC-001", "Nova Dental", "Medical", "180", 3200, 34.0, "2028-09-30", "Local", 620),
    ("SC-001", "Desert Nail Bar", "Service", "190", 1800, 38.5, "2027-05-31", "Local", 480),
    ("SC-001", "Cafe Luna", "Restaurant", "200", 2600, 41.0, "2026-11-30", "Local", 690),
    ("SC-001", "Pet Pantry", "Pet", "220", 5200, 24.25, "2030-03-31", "Regional", 360),
    ("SC-001", "Available", "Vacant", "230", 5100, 0.0, "2026-12-31", "None", 0),
    ("SC-002", "Market Street Grocer", "Grocery", "100", 39200, 16.25, "2030-10-31", "Regional", 470),
    ("SC-002", "HomeStyle Goods", "Home", "150", 24000, 14.75, "2028-04-30", "National", 255),
    ("SC-002", "KidsCare Clinic", "Medical", "210", 6200, 29.5, "2032-01-31", "Regional", 540),
    ("SC-002", "Salon Mint", "Service", "230", 2200, 32.0, "2027-08-31", "Local", 405),
    ("SC-002", "Grill House", "Restaurant", "240", 4100, 35.25, "2029-02-28", "Local", 610),
    ("SC-002", "Available", "Vacant", "260", 12800, 0.0, "2026-12-31", "None", 0),
    ("SC-003", "Urban Organics", "Grocery", "100", 18200, 31.5, "2034-07-31", "National", 720),
    ("SC-003", "CorePower Studio", "Fitness", "130", 7400, 36.75, "2031-03-31", "National", 455),
    ("SC-003", "Bluebird Cafe", "Restaurant", "150", 2800, 52.0, "2028-10-31", "Local", 820),
    ("SC-003", "Austin Eye Co.", "Medical", "170", 3600, 48.5, "2030-06-30", "Regional", 700),
    ("SC-003", "The UPS Store", "Service", "180", 1600, 45.0, "2029-09-30", "National", 590),
    ("SC-003", "Available", "Vacant", "190", 2000, 0.0, "2026-12-31", "None", 0),
]

MARKET_ROWS = [
    ("SC-001", 3, 147500, 82750, 6.2, 38200, 36.4),
    ("SC-002", 3, 166900, 91300, 8.4, 42100, 38.1),
    ("SC-003", 3, 128400, 124600, 9.7, 51400, 34.8),
]

COMP_ROWS = [
    ("SC-001", "Bell Road Shops", 2.7, 17300000, 0.0675, 238, "2025-08-15"),
    ("SC-001", "Cactus Crossing", 4.1, 22100000, 0.0640, 252, "2026-02-10"),
    ("SC-001", "Arcadia Service Center", 5.8, 15800000, 0.0610, 279, "2025-11-21"),
    ("SC-002", "Pineville Commons", 3.2, 24200000, 0.0660, 223, "2025-09-18"),
    ("SC-002", "Providence Shops", 6.5, 31200000, 0.0625, 258, "2026-04-02"),
    ("SC-002", "Matthews Village", 5.4, 28600000, 0.0645, 241, "2026-01-12"),
    ("SC-003", "Lamar Retail Row", 2.1, 21400000, 0.0550, 337, "2025-12-05"),
    ("SC-003", "South Congress Shops", 4.4, 29800000, 0.0525, 371, "2026-05-16"),
    ("SC-003", "Zilker Service Plaza", 3.8, 19600000, 0.0570, 322, "2025-10-30"),
]


@dataclass(frozen=True)
class ValuationResult:
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


st.set_page_config(page_title="RetailScope AI", page_icon="RS", layout="wide")

SENTRY_ENABLED = False


CSS = """
<style>
.block-container { padding-top: 1.25rem; max-width: 1480px; }
[data-testid="stMetric"] { background: #fff; border: 1px solid #d9ddd5; border-radius: 8px; padding: 12px 14px; min-height: 96px; }
[data-testid="stMetricLabel"] p { color: #4f5c57; font-size: 0.82rem; }
[data-testid="stMetricValue"] { color: #17211f; font-size: 1.45rem; }
.badge { display: inline-flex; border-radius: 999px; border: 1px solid #bed4cd; background: #eaf3ef; color: #126c67; padding: 4px 9px; font-size: .78rem; font-weight: 650; }
.subtitle { color: #59665f; margin: .35rem 0 1rem; }
.section-label { color: #43504b; font-size: .78rem; font-weight: 700; margin: 1rem 0 .35rem; text-transform: uppercase; }
.note { color: #64716c; font-size: .86rem; }
.copy-label { color: #43504b; font-size: .82rem; font-weight: 700; margin-top: .35rem; text-transform: uppercase; }
</style>
"""


def init_sentry() -> bool:
    if sentry_sdk is None:
        return False
    if sentry_sdk.is_initialized():
        return True

    dsn = get_sentry_dsn()
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=get_config_value("SENTRY_ENVIRONMENT", "snowflake"),
        release=get_config_value("SENTRY_RELEASE", None),
        traces_sample_rate=float(get_config_value("SENTRY_TRACES_SAMPLE_RATE", "0.2")),
        profiles_sample_rate=float(get_config_value("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
        send_default_pii=False,
        before_send=scrub_event,
    )
    return True


def get_sentry_dsn() -> str | None:
    value = get_config_value("SENTRY_DSN", None)
    if value:
        return value

    try:
        from _snowflake import get_generic_secret_string

        return get_generic_secret_string("SENTRY_DSN")
    except Exception:
        return None


def get_config_value(name: str, default: str | None) -> str | None:
    if os.getenv(name):
        return os.getenv(name)
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    try:
        sentry_config = st.secrets.get("sentry", {})
        key = name.removeprefix("SENTRY_").lower()
        if key in sentry_config:
            return str(sentry_config[key])
    except Exception:
        pass
    return default


def set_agent_context(property_id: str, property_name: str, data_source: str) -> None:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        return
    sentry_sdk.set_tag("agent.kind", "retailscope-ai")
    sentry_sdk.set_tag("property.id", property_id)
    sentry_sdk.set_tag("data.source", data_source)
    sentry_sdk.set_context(
        "property",
        {"id": property_id, "name": property_name, "data_source": data_source},
    )


@contextmanager
def start_span(op: str, name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        yield None
        return
    with sentry_sdk.start_span(op=op, name=name) as span:
        if data:
            for key, value in data.items():
                span.set_data(key, value)
        yield span


@contextmanager
def start_agent_transaction(name: str, data: dict[str, Any] | None = None) -> Iterator[Any]:
    if sentry_sdk is None or not sentry_sdk.is_initialized():
        yield None
        return
    with sentry_sdk.start_transaction(op="ai.pipeline", name=name) as transaction:
        if data:
            for key, value in data.items():
                transaction.set_data(key, value)
        yield transaction


def capture_exception(exc: Exception) -> None:
    if sentry_sdk is not None and sentry_sdk.is_initialized():
        sentry_sdk.capture_exception(exc)


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    return scrub_value(event)


def scrub_value(value: Any) -> Any:
    sensitive = {"password", "token", "secret", "dsn", "authorization", "cookie", "question", "prompt", "messages", "tenant", "tenants"}
    if isinstance(value, dict):
        return {
            key: "[Filtered]" if str(key).lower() in sensitive else scrub_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    return value


def main() -> None:
    global SENTRY_ENABLED
    SENTRY_ENABLED = init_sentry()
    st.markdown(CSS, unsafe_allow_html=True)
    view = st.radio(
        "View",
        ["Underwriting Desk", "MCP Install"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if view == "MCP Install":
        render_mcp_install_page()
        return

    with start_span("db.load", "Load shopping center dataset"):
        properties, tenants_all, markets, comps_all, source = load_data()

    options = {
        f"{row.name} · {row.city}, {row.state}": row.property_id
        for row in properties.itertuples()
    }
    header, selector = st.columns([0.72, 0.28], vertical_alignment="center")
    with header:
        st.markdown('<span class="badge">Snowflake Deployment</span>', unsafe_allow_html=True)
        st.title("RetailScope AI")
        st.markdown(
            '<div class="subtitle">AI-powered shopping-center underwriting for rents, trade areas, comps, and acquisition value.</div>',
            unsafe_allow_html=True,
        )
    with selector:
        selected_label = st.selectbox("Shopping center", list(options.keys()), label_visibility="collapsed")

    selected_id = options[selected_label]
    property_row = properties.loc[properties["property_id"] == selected_id].iloc[0]
    tenants = tenants_all.loc[tenants_all["property_id"] == selected_id].copy()
    market = markets.loc[markets["property_id"] == selected_id].iloc[0]
    comps = comps_all.loc[comps_all["property_id"] == selected_id].copy()
    set_agent_context(str(property_row["property_id"]), str(property_row["name"]), source)
    with start_span("app.valuation", "Build commercial real estate valuation", {"property_id": str(property_row["property_id"])}):
        valuation = build_valuation(property_row, tenants, market, comps)

    if st.session_state.get("active_property_id") != selected_id:
        st.session_state.active_property_id = selected_id
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "I loaded the center. Ask about price, rents, rollover, demographics, or diligence focus.",
            }
        ]

    chat_col, preview_col = st.columns([0.53, 0.47], gap="large")
    with chat_col:
        render_chat(property_row, tenants, market, comps, valuation)
    with preview_col:
        render_preview(property_row, tenants, market, comps, valuation, source)


@st.cache_data(ttl=300)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    session = get_session()
    if session is not None:
        try:
            tables = {
                "properties": "SHOPPING_CENTER_PROPERTIES",
                "tenants": "SHOPPING_CENTER_TENANTS",
                "markets": "SHOPPING_CENTER_MARKETS",
                "comps": "SHOPPING_CENTER_SALES_COMPS",
            }
            data = {name: normalize(session.table(table).to_pandas()) for name, table in tables.items()}
            data["tenants"]["lease_end"] = pd.to_datetime(data["tenants"]["lease_end"])
            data["comps"]["sale_date"] = pd.to_datetime(data["comps"]["sale_date"])
            return data["properties"], data["tenants"], data["markets"], data["comps"], "Snowflake tables"
        except Exception:
            pass

    properties = pd.DataFrame(PROPERTY_ROWS)
    tenants = pd.DataFrame(
        TENANT_ROWS,
        columns=["property_id", "tenant", "category", "suite", "area_sf", "rent_psf", "lease_end", "credit", "sales_psf"],
    )
    markets = pd.DataFrame(
        MARKET_ROWS,
        columns=["property_id", "trade_area_miles", "population", "median_income", "growth_5yr_pct", "traffic_count", "median_age"],
    )
    comps = pd.DataFrame(
        COMP_ROWS,
        columns=["property_id", "comp_name", "distance_miles", "sale_price", "cap_rate", "price_psf", "sale_date"],
    )
    tenants["lease_end"] = pd.to_datetime(tenants["lease_end"])
    comps["sale_date"] = pd.to_datetime(comps["sale_date"])
    return properties, tenants, markets, comps, "Embedded demo data"


def get_session():
    try:
        from snowflake.snowpark.context import get_active_session

        return get_active_session()
    except Exception:
        return None


def render_chat(property_row, tenants, market, comps, valuation) -> None:
    st.subheader("Agent Chat")
    st.caption("Powered by Snowflake Cortex when available, with deterministic underwriting fallback.")

    examples = [
        "Should we acquire this center at asking price?",
        "Where is the rent upside?",
        "What risks should diligence focus on?",
    ]
    for col, example in zip(st.columns(3), examples):
        if col.button(example, width="stretch"):
            submit_question(example, property_row, tenants, market, comps, valuation)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask RetailScope AI")
    if prompt:
        submit_question(prompt, property_row, tenants, market, comps, valuation)
        st.rerun()


def submit_question(question, property_row, tenants, market, comps, valuation) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with start_agent_transaction(
        "retailscope_ai.answer",
        {
            "property_id": str(property_row["property_id"]),
            "question_length": len(question),
            "tenant_count": int(len(tenants)),
            "comp_count": int(len(comps)),
        },
    ):
        with st.status("Underwriting the answer...", expanded=True) as status:
            st.write("Reading property, rent roll, trade area, and comps.")
            st.write("Running Cortex or local underwriting fallback.")
            answer = answer_agent(question, property_row, tenants, market, comps, valuation)
            status.update(label="Answer ready", state="complete", expanded=False)
    st.session_state.messages.append({"role": "assistant", "content": answer})


def render_preview(property_row, tenants, market, comps, valuation, source) -> None:
    st.subheader(property_row["name"])
    st.caption(f"{property_row['address']}, {property_row['city']}, {property_row['state']} · Data source: {source}")
    cols = st.columns(4)
    cols[0].metric("Blended Value", money(valuation.blended_value), f"{valuation.equity_spread:.1%} vs ask")
    cols[1].metric("Value / SF", f"${valuation.value_psf:,.0f}", f"{valuation.cap_rate:.2%} cap")
    cols[2].metric("Stabilized NOI", money(valuation.stabilized_noi), f"{valuation.occupancy:.0%} occupied")
    cols[3].metric("Market Rent", f"${valuation.market_rent_psf:,.2f}", f"${valuation.current_rent_psf:,.2f} in-place")

    st.markdown('<div class="section-label">Acquisition Read</div>', unsafe_allow_html=True)
    st.info(f"{valuation.recommendation}. Indicated value is {money(valuation.blended_value)} versus asking price of {money(valuation.asking_price)}.")

    tab_model, tab_rent, tab_market, tab_comps = st.tabs(["Model", "Rent Roll", "Market", "Comps"])
    with tab_model:
        bridge = pd.DataFrame(
            {
                "Method": ["Cap-rate value", "Sales comp value", "DCF value", "Blended value", "Asking price"],
                "Value": [valuation.cap_rate_value, valuation.sales_comp_value, valuation.dcf_value, valuation.blended_value, valuation.asking_price],
            }
        )
        st.bar_chart(bridge.set_index("Method"))
        left, right = st.columns(2)
        with left:
            st.markdown('<div class="section-label">Risks</div>', unsafe_allow_html=True)
            for item in valuation.risks:
                st.write(f"- {item}")
        with right:
            st.markdown('<div class="section-label">Opportunities</div>', unsafe_allow_html=True)
            for item in valuation.opportunities:
                st.write(f"- {item}")
    with tab_rent:
        display = tenants.copy()
        display["annual_rent"] = display["area_sf"] * display["rent_psf"]
        st.dataframe(display, hide_index=True, width="stretch")
    with tab_market:
        cols = st.columns(3)
        cols[0].metric("Population", f"{int(market['population']):,}", f"{market['trade_area_miles']} mi")
        cols[1].metric("Median Income", money(float(market["median_income"])), f"{market['growth_5yr_pct']:.1f}% growth")
        cols[2].metric("Traffic Count", f"{int(market['traffic_count']):,}", f"Age {market['median_age']:.1f}")
    with tab_comps:
        st.dataframe(comps, hide_index=True, width="stretch")


def answer_agent(question, property_row, tenants, market, comps, valuation) -> str:
    session = get_session()
    if session is not None:
        try:
            from snowflake.snowpark.functions import ai_complete

            with start_span("ai.prompt.build", "Build appraisal prompt"):
                payload = {
                    "question": question,
                    "property": series_dict(property_row),
                    "tenants": records(tenants),
                    "market": series_dict(market),
                    "sales_comps": records(comps),
                    "valuation": valuation.__dict__,
                }
                prompt = (
                    "You are an institutional commercial real estate acquisition analyst. "
                    "Use only the provided data. Be concise and cite metrics behind conclusions.\n\n"
                    f"{json.dumps(payload, default=str)}"
                )
            with start_span(
                "ai.chat_completions.create.snowflake_cortex",
                "Snowflake Cortex ai_complete",
                {"model": "claude-sonnet-5", "provider": "snowflake_cortex"},
            ):
                response = session.range(1).select(ai_complete("claude-sonnet-5", prompt).alias("response")).collect()[0][0]
            return extract_text(response)
        except Exception as exc:
            capture_exception(exc)
            st.caption(f"Cortex unavailable; using deterministic local analyst. Details: {exc}")
    with start_span("ai.fallback", "Deterministic local appraisal answer", {"reason": "cortex_unavailable_or_unconfigured"}):
        return answer_locally(question, property_row, tenants, market, comps, valuation)


def answer_locally(question, property_row, tenants, market, comps, valuation) -> str:
    q = question.lower()
    occupied = tenants[tenants["category"].str.lower() != "vacant"]
    top = occupied.nlargest(3, "area_sf")
    top_text = ", ".join(f"{row.tenant} at {int(row.area_sf):,} sf and ${row.rent_psf:,.2f}/sf" for row in top.itertuples())

    if any(token in q for token in ("rent", "tenant", "rollover", "lease")):
        return (
            f"{property_row['name']} is {valuation.occupancy:.0%} occupied with in-place rent of "
            f"${valuation.current_rent_psf:,.2f}/sf versus estimated market rent of ${valuation.market_rent_psf:,.2f}/sf. "
            f"The largest tenants are {top_text}. Stabilized NOI is {money(valuation.stabilized_noi)}."
        )
    if any(token in q for token in ("demographic", "income", "population", "trade area")):
        return (
            f"The 3-mile trade area has {int(market['population']):,} people, median household income of "
            f"{money(float(market['median_income']))}, {market['growth_5yr_pct']:.1f}% five-year growth, "
            f"and traffic of {int(market['traffic_count']):,} vehicles per day."
        )
    return (
        f"My indicated value is {money(valuation.blended_value)}, or ${valuation.value_psf:,.0f}/sf. "
        f"Against asking price of {money(valuation.asking_price)}, the spread is {valuation.equity_spread:.1%}. "
        f"Recommendation: {valuation.recommendation}. Key risks: {', '.join(valuation.risks[:2])}."
    )


def build_valuation(property_row, tenants, market, comps) -> ValuationResult:
    gla = float(property_row["gla_sf"])
    occupied = tenants[tenants["category"].str.lower() != "vacant"].copy()
    gross_rent = float((occupied["area_sf"] * occupied["rent_psf"]).sum())
    vacancy_loss = gross_rent * max(0.02, 1 - float(property_row["occupancy_pct"]) / 100)
    noi = gross_rent + gross_rent * 0.22 - vacancy_loss - gross_rent * 0.31
    current_rent_psf = weighted_average(occupied, "rent_psf", "area_sf")
    market_rent_psf = estimate_market_rent_psf(current_rent_psf, market, comps)
    vacant_area = float(tenants.loc[tenants["category"].str.lower() == "vacant", "area_sf"].sum())
    stabilized_noi = noi + max(0, market_rent_psf - current_rent_psf) * float(occupied["area_sf"].sum()) * 0.62 + vacant_area * market_rent_psf * 0.62
    cap_rate = risk_adjusted_cap_rate(property_row, tenants, market, comps)
    cap_rate_value = stabilized_noi / cap_rate
    sales_comp_value = float(comps["price_psf"].median()) * gla
    dcf_value = discounted_cash_flow(stabilized_noi, cap_rate)
    blended_value = cap_rate_value * 0.5 + sales_comp_value * 0.25 + dcf_value * 0.25
    asking_price = float(property_row["asking_price"])
    equity_spread = (blended_value - asking_price) / asking_price
    risks = identify_risks(property_row, tenants, market, cap_rate)
    opportunities = identify_opportunities(current_rent_psf, market_rent_psf, tenants, market)
    if equity_spread >= 0.12:
        recommendation = "Acquire / strong upside"
    elif equity_spread >= 0.03:
        recommendation = "Pursue with price discipline"
    elif equity_spread >= -0.05:
        recommendation = "Market value / diligence heavy"
    else:
        recommendation = "Pass unless repriced"
    return ValuationResult(noi, stabilized_noi, market_rent_psf, current_rent_psf, float(property_row["occupancy_pct"]) / 100, cap_rate, cap_rate_value, sales_comp_value, dcf_value, blended_value, blended_value / gla, asking_price, equity_spread, recommendation, risks, opportunities)


def estimate_market_rent_psf(current_rent_psf, market, comps) -> float:
    income_factor = min(1.18, max(0.92, float(market["median_income"]) / 95000))
    growth_factor = 1 + float(market["growth_5yr_pct"]) / 100 * 0.45
    comp_factor = min(1.12, max(0.9, float(comps["price_psf"].median()) / 280))
    return current_rent_psf * income_factor * growth_factor * comp_factor


def risk_adjusted_cap_rate(property_row, tenants, market, comps) -> float:
    base = float(comps["cap_rate"].median())
    occupancy_penalty = max(0, 0.94 - float(property_row["occupancy_pct"]) / 100) * 0.045
    income_credit = max(0, (float(market["median_income"]) - 95000) / 100000) * 0.004
    rollover_penalty = near_term_rollover_pct(tenants) * 0.012
    return min(0.0825, max(0.0475, base + occupancy_penalty + rollover_penalty - income_credit))


def discounted_cash_flow(stabilized_noi, cap_rate) -> float:
    discount_rate = cap_rate + 0.021
    noi = stabilized_noi
    cash_flows = []
    for year in range(1, 11):
        noi *= 1.0275
        cash_flows.append(noi / ((1 + discount_rate) ** year))
    terminal_value = noi * 1.02 / max(0.045, cap_rate + 0.0025)
    return sum(cash_flows) + terminal_value / ((1 + discount_rate) ** 10)


def identify_risks(property_row, tenants, market, cap_rate) -> list[str]:
    risks = []
    if float(property_row["occupancy_pct"]) < 92:
        risks.append("occupancy below institutional target")
    if near_term_rollover_pct(tenants) > 0.18:
        risks.append("meaningful near-term lease rollover")
    if float(market["median_income"]) < 85000:
        risks.append("trade-area income below preferred threshold")
    if cap_rate > 0.0675:
        risks.append("market requires a wider risk premium")
    return risks or ["no single dominant red flag in current underwriting"]


def identify_opportunities(current_rent_psf, market_rent_psf, tenants, market) -> list[str]:
    opportunities = []
    if market_rent_psf > current_rent_psf * 1.05:
        opportunities.append("mark below-market leases toward current rent")
    if tenants["category"].str.lower().eq("vacant").any():
        opportunities.append("lease vacant suites to lift stabilized NOI")
    if float(market["growth_5yr_pct"]) > 7:
        opportunities.append("lean into household-growth-driven rent increases")
    return opportunities or ["preserve occupancy and manage expenses tightly"]


def near_term_rollover_pct(tenants) -> float:
    occupied = tenants[tenants["category"].str.lower() != "vacant"].copy()
    lease_end = pd.to_datetime(occupied["lease_end"], errors="coerce")
    cutoff = pd.Timestamp(date.today()).normalize() + pd.DateOffset(years=3)
    rolling_area = float(occupied.loc[lease_end <= cutoff, "area_sf"].sum())
    total_area = float(occupied["area_sf"].sum())
    return rolling_area / total_area if total_area else 0


def weighted_average(df, value_col, weight_col) -> float:
    weights = df[weight_col].astype(float)
    return float((df[value_col].astype(float) * weights).sum() / weights.sum()) if weights.sum() else 0


def normalize(df):
    df = df.copy()
    df.columns = [str(column).lower() for column in df.columns]
    return df


def series_dict(series) -> dict:
    return {key: json_safe(value) for key, value in series.to_dict().items()}


def records(df) -> list[dict]:
    return [{key: json_safe(value) for key, value in record.items()} for record in df.to_dict(orient="records")]


def json_safe(value):
    if hasattr(value, "item"):
        return value.item()
    return value


def extract_text(response) -> str:
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return response
    else:
        parsed = response
    if isinstance(parsed, dict):
        choices = parsed.get("choices")
        if choices and isinstance(choices, list):
            message = choices[0].get("messages") or choices[0].get("message") or choices[0].get("text")
            if isinstance(message, dict):
                return str(message.get("content", ""))
            return str(message or "")
        return str(parsed.get("response") or parsed.get("text") or parsed)
    return str(parsed)


def money(value) -> str:
    return f"${value:,.0f}"


def render_mcp_install_page() -> None:
    repo_url = os.getenv(
        "RETAILSCOPE_REPO_URL",
        "https://github.com/iker-ai-dev/RealStateAgentApprecier.git",
    )
    server_command = f"uvx --from git+{repo_url} retailscope-mcp"
    generic_config = {
        "mcpServers": {
            "retailscope-ai": {
                "command": "uvx",
                "args": ["--from", f"git+{repo_url}", "retailscope-mcp"],
            }
        }
    }

    st.markdown('<span class="badge">MCP Server</span>', unsafe_allow_html=True)
    st.title("RetailScope AI")
    st.markdown(
        '<div class="subtitle">Install RetailScope AI in your coding agent and query shopping-center rent rolls, market context, comps, valuation outputs, and the appraisal answer layer.</div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([0.48, 0.52], gap="large")
    with left:
        st.subheader("Tools")
        st.markdown(
            """
            - `list_shopping_centers`
            - `get_shopping_center_context`
            - `appraise_shopping_center`
            - `ask_retailscope`
            """
        )
        st.markdown('<div class="section-label">Server command</div>', unsafe_allow_html=True)
        st.code(server_command, language="bash")
        st.caption("Requires `uv`/`uvx` on the agent machine. The MCP server runs over stdio.")

    with right:
        st.subheader("Copy-Paste Install")
        commands = {
            "Codex": f"codex mcp add retailscope-ai -- {server_command}",
            "Claude Code": f"claude mcp add retailscope-ai -- {server_command}",
            "OpenCode": f"opencode mcp add retailscope-ai -- {server_command}",
            "Generic JSON": json.dumps(generic_config, indent=2),
        }
        tabs = st.tabs(list(commands.keys()))
        for tab, (label, command) in zip(tabs, commands.items()):
            with tab:
                language = "json" if label == "Generic JSON" else "bash"
                st.markdown(f'<div class="copy-label">{label}</div>', unsafe_allow_html=True)
                st.code(command, language=language)

    st.markdown('<div class="section-label">Example prompt</div>', unsafe_allow_html=True)
    st.code(
        "Use RetailScope AI to appraise SC-001 and tell me whether the rent roll supports the asking price.",
        language="text",
    )


if __name__ == "__main__":
    main()
