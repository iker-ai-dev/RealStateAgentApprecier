from __future__ import annotations

import json
import os
import math
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from app.observability import init_sentry, start_agent_transaction
from app.acquisition_policy import (GATE, DISCLAIMER, context_fingerprint, validate_model_inputs, diligence_flags, render_research_answer)
from app.services.topic_router import route_topic
from app.data_access import get_snowflake_config
from app.research_ui import render_research
from app.deal_radar_ui import render_deal_radar
from app.presentation import apply_presentation, navigation, render_analysis_header, render_analysis_mode


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

    def __post_init__(self):
        for value in self.__dict__.values():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError("Illustrative model produced a nonfinite value; correct inputs.")



st.set_page_config(page_title="RetailScope AI", page_icon="RS", layout="wide")





def main() -> None:
    apply_presentation()
    init_sentry()
    page = st.navigation([
        st.Page(render_workspace, title="RetailScope AI", default=True),
        st.Page(render_mcp_install_page, title="MCP Install", url_path="landing"),
    ])
    page.run()


def render_workspace() -> None:
    if navigation() == "Find deals":
        render_deal_radar()
        return
    render_analysis_header()
    properties, tenants_all, markets, comps_all, source = load_data()

    options = {
        f"{row.name} · {row.city}, {row.state}": row.property_id
        for row in properties.itertuples()
    }
    header, selector = st.columns([0.72, 0.28], vertical_alignment="center")
    with header:
        st.subheader("Selected center")
        st.caption("Inspect supplied records and illustrative model assumptions.")
    with selector:
        selected_label = st.selectbox("Shopping center", list(options.keys()), label_visibility="collapsed")

    selected_id = options[selected_label]
    property_row = properties.loc[properties["property_id"] == selected_id].iloc[0]
    tenants = tenants_all.loc[tenants_all["property_id"] == selected_id].copy()
    market = markets.loc[markets["property_id"] == selected_id].iloc[0]
    comps = comps_all.loc[comps_all["property_id"] == selected_id].copy()
    try:
        valuation = build_valuation(property_row, tenants, market, comps)
    except (ValueError, KeyError, TypeError, OverflowError):
        st.error("Dataset inputs are incomplete or invalid. Correct the property/rent/comparison data before calculating.")
        st.stop()
    render_analysis_mode(source)
    render_research(str(property_row["city"]) + ", " + str(property_row["state"]))

    fingerprint = context_fingerprint(property_row, tenants, market, comps, source)
    if st.session_state.get("active_context_fingerprint") != fingerprint:
        st.session_state.active_context_fingerprint = fingerprint
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
            data = {name: normalize(session.table(table).limit(10001).to_pandas()) for name, table in tables.items()}
            if any(len(frame) > 10000 for frame in data.values()):
                raise ValueError("Dataset exceeds bounded demo load.")
            data["tenants"]["lease_end"] = pd.to_datetime(data["tenants"]["lease_end"])
            data["comps"]["sale_date"] = pd.to_datetime(data["comps"]["sale_date"])
            return data["properties"], data["tenants"], data["markets"], data["comps"], "Snowflake tables"
        except Exception:
            st.warning("Snowflake tables could not be loaded; explicitly using synthetic demo data.")

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
    model = get_snowflake_config().get("cortex_model", "")
    configured = isinstance(model, str) and bool(model.strip())
    st.caption("Hackathon Cortex model configured. Financial facts are host-rendered." if configured else "Hackathon model not configured. Deterministic fallback; no LLM generation.")

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
    with st.status("Underwriting the answer...", expanded=True) as status:
        st.write("Reading property, rent roll, trade area, and comps.")
        st.write("Running Cortex or local underwriting fallback.")
        answer = answer_agent(question, property_row, tenants, market, comps, valuation)
        status.update(label="Answer ready", state="complete", expanded=False)
    st.session_state.messages.append({"role": "assistant", "content": answer})


def render_preview(property_row, tenants, market, comps, valuation, source) -> None:
    st.subheader(property_row["name"])
    st.caption(f"{property_row['address']}, {property_row['city']}, {property_row['state']} · Data source: {source}")
    cols = st.columns(2) + st.columns(2)
    cols[0].metric("Illustrative Value", money(valuation.blended_value), f"{valuation.equity_spread:.1%} vs ask")
    cols[1].metric("Value / SF", f"${valuation.value_psf:,.0f}", f"{valuation.cap_rate:.2%} cap")
    cols[2].metric("Illustrative NOI", money(valuation.stabilized_noi), f"{valuation.occupancy:.0%} supplied occupancy")
    cols[3].metric("Heuristic Rent", f"${valuation.market_rent_psf:,.2f}", f"${valuation.current_rent_psf:,.2f} in-place")

    st.markdown('<div class="section-label">Acquisition Read</div>', unsafe_allow_html=True)
    st.info(GATE + ". Legacy value spread is not an investor return.")
    with st.expander("Evidence gaps and model diagnostics"):
        for flag in diligence_flags(property_row, tenants):
            st.caption(flag)

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
        cols[1].metric("Median Income", money(float(market["median_income"])), "Vintage and dollar year unknown")
        cols[2].metric("Traffic Count", f"{int(market['traffic_count']):,}", f"Age {market['median_age']:.1f}")
    with tab_comps:
        st.dataframe(comps, hide_index=True, width="stretch")


def answer_agent(question, property_row, tenants, market, comps, valuation) -> str:
    with start_agent_transaction("retailscope_ai.answer"):
        model = get_snowflake_config().get("cortex_model", "")
        session = get_session() if isinstance(model, str) and model.strip() else None
        topic, provider = route_topic(question, session, model)
    return render_research_answer(topic, property_row, tenants, market, comps, valuation, provider=provider)


def answer_locally(question, property_row, tenants, market, comps, valuation) -> str:
    topic, provider = route_topic(question)
    return render_research_answer(topic, property_row, tenants, market, comps, valuation, provider=provider)


def build_valuation(property_row, tenants, market, comps) -> ValuationResult:
    validate_model_inputs(property_row, tenants, market, comps)
    gla = float(property_row["gla_sf"])
    occupied = tenants[tenants["category"].str.lower() != "vacant"].copy()
    gross_rent = float((occupied["area_sf"] * occupied["rent_psf"]).sum())
    vacancy_loss = 0.0  # Occupied rent already excludes physical vacancy; collection loss is unknown.
    noi = gross_rent + gross_rent * 0.22 - vacancy_loss - gross_rent * 0.31
    current_rent_psf = weighted_average(occupied, "rent_psf", "area_sf")
    market_rent_psf = estimate_market_rent_psf(current_rent_psf, market, comps)
    vacant_area = float(tenants.loc[tenants["category"].str.lower() == "vacant", "area_sf"].sum())
    stabilized_noi = noi + max(0, market_rent_psf - current_rent_psf) * float(occupied["area_sf"].sum()) * 0.62 + vacant_area * market_rent_psf * 0.88 * 0.62
    cap_rate = risk_adjusted_cap_rate(property_row, tenants, market, comps)
    cap_rate_value = stabilized_noi / cap_rate
    sales_comp_value = float(comps["price_psf"].median()) * gla
    dcf_value = discounted_cash_flow(stabilized_noi, cap_rate)
    blended_value = cap_rate_value * 0.5 + sales_comp_value * 0.25 + dcf_value * 0.25
    asking_price = float(property_row["asking_price"])
    equity_spread = (blended_value - asking_price) / asking_price
    risks = identify_risks(property_row, tenants, market, cap_rate)
    opportunities = identify_opportunities(current_rent_psf, market_rent_psf, tenants, market)
    recommendation = GATE
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
    unknown = pd.to_datetime(tenants.loc[tenants["category"].str.lower() != "vacant", "lease_end"], errors="coerce").isna().any()
    if unknown:
        risks.append("lease expiration evidence is incomplete; rollover exposure remains unknown")
    return risks + ["unverified inputs and incomplete financial diligence prevent a risk-clearance conclusion"]


def identify_opportunities(current_rent_psf, market_rent_psf, tenants, market) -> list[str]:
    opportunities = []
    if market_rent_psf > current_rent_psf * 1.05:
        opportunities.append("test the illustrative rent gap against executed lease comps and lease terms; below-market rent is not established")
    if tenants["category"].str.lower().eq("vacant").any():
        opportunities.append("investigate vacant-suite demand, TI/LC and carry before assuming lease-up")
    if float(market["growth_5yr_pct"]) > 7:
        opportunities.append("verify dated population trends and local retail demand; rent growth is not established")
    return opportunities or ["no supported value-add thesis established; obtain operating and leasing evidence"]


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
