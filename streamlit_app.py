from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app.data_access import load_dataset
from app.observability import init_sentry, set_agent_context, start_span
from app.services.cortex import answer_agent
from app.services.valuation import ValuationResult, build_valuation


st.set_page_config(
    page_title="MLH Real Estate Agent Appraiser",
    page_icon="MLH",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SENTRY_ENABLED = init_sentry()


CSS = """
<style>
    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 1.2rem;
        max-width: 1480px;
    }
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #d9ddd5;
        border-radius: 8px;
        padding: 12px 14px;
        min-height: 96px;
    }
    [data-testid="stMetricLabel"] p {
        color: #4f5c57;
        font-size: 0.82rem;
    }
    [data-testid="stMetricValue"] {
        color: #17211f;
        font-size: 1.45rem;
    }
    .app-title {
        display: flex;
        align-items: center;
        gap: .75rem;
        margin-bottom: .35rem;
    }
    .app-title h1 {
        font-size: 1.85rem;
        margin: 0;
        letter-spacing: 0;
    }
    .subtitle {
        color: #59665f;
        margin-bottom: 1rem;
    }
    .badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        border: 1px solid #bed4cd;
        background: #eaf3ef;
        color: #126c67;
        padding: 4px 9px;
        font-size: .78rem;
        font-weight: 650;
    }
    .section-label {
        color: #43504b;
        font-size: .78rem;
        font-weight: 700;
        margin: 1rem 0 .35rem 0;
        text-transform: uppercase;
    }
    .note {
        color: #64716c;
        font-size: .86rem;
    }
</style>
"""


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    with start_span("db.load", "Load shopping center dataset"):
        dataset = load_dataset()

    property_options = {
        f"{row.name} · {row.city}, {row.state}": row.property_id
        for row in dataset.properties.itertuples()
    }

    top_left, top_right = st.columns([0.72, 0.28], vertical_alignment="center")
    with top_left:
        st.markdown(
            """
            <div class="app-title">
              <span class="badge">Acquisition Desk</span>
              <h1>Real Estate Agent Appraiser</h1>
            </div>
            <div class="subtitle">
              Query rent rolls, demographics, comparable sales, and valuation assumptions for open-air shopping centers.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with top_right:
        selected_label = st.selectbox("Shopping center", list(property_options.keys()), label_visibility="collapsed")

    selected_property_id = property_options[selected_label]
    property_row = dataset.properties.loc[
        dataset.properties["property_id"] == selected_property_id
    ].iloc[0]
    tenants = dataset.tenants.loc[dataset.tenants["property_id"] == selected_property_id].copy()
    market = dataset.markets.loc[dataset.markets["property_id"] == selected_property_id].iloc[0]
    comps = dataset.sales_comps.loc[dataset.sales_comps["property_id"] == selected_property_id].copy()
    set_agent_context(
        str(property_row["property_id"]),
        str(property_row["name"]),
        dataset.source,
    )
    with start_span(
        "app.valuation",
        "Build commercial real estate valuation",
        {"property_id": str(property_row["property_id"])},
    ):
        valuation = build_valuation(property_row, tenants, market, comps)

    _ensure_chat_state(selected_property_id)

    chat_col, preview_col = st.columns([0.53, 0.47], gap="large")
    with chat_col:
        _render_chat(property_row, tenants, market, comps, valuation)

    with preview_col:
        _render_preview(property_row, tenants, market, comps, valuation, dataset.source)


def _ensure_chat_state(property_id: str) -> None:
    if st.session_state.get("active_property_id") != property_id:
        st.session_state.active_property_id = property_id
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "I loaded the center. Ask about purchase price, rent upside, tenant rollover, "
                    "trade-area quality, or what diligence would change the valuation."
                ),
            }
        ]


def _render_chat(
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
) -> None:
    st.subheader("Agent Chat")
    st.caption("Answers use the selected center, rent roll, trade area, sales comps, and underwriting model.")

    prompt_buttons = st.columns(3)
    examples = [
        "Should we acquire this center at asking price?",
        "Where is the rent upside?",
        "What risks should diligence focus on?",
    ]
    for column, example in zip(prompt_buttons, examples):
        if column.button(example, width="stretch"):
            _submit_question(example, property_row, tenants, market, comps, valuation)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_prompt = st.chat_input("Ask the appraiser agent")
    if user_prompt:
        _submit_question(user_prompt, property_row, tenants, market, comps, valuation)
        st.rerun()


def _submit_question(
    question: str,
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.status("Underwriting the answer...", expanded=True) as status:
        st.write("Reading the selected property context.")
        st.write("Running valuation and agent reasoning.")
        answer = answer_agent(question, property_row, tenants, market, comps, valuation)
        status.update(label="Answer ready", state="complete", expanded=False)
    st.session_state.messages.append({"role": "assistant", "content": answer})


def _render_preview(
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
    source: str,
) -> None:
    st.subheader(property_row["name"])
    st.caption(f"{property_row['address']}, {property_row['city']}, {property_row['state']} · Data source: {source}")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Blended Value", _money(valuation.blended_value), f"{valuation.equity_spread:.1%} vs ask")
    metric_cols[1].metric("Value / SF", f"${valuation.value_psf:,.0f}", f"{valuation.cap_rate:.2%} cap")
    metric_cols[2].metric("Stabilized NOI", _money(valuation.stabilized_noi), f"{valuation.occupancy:.0%} occupied")
    metric_cols[3].metric("Market Rent", f"${valuation.market_rent_psf:,.2f}", f"${valuation.current_rent_psf:,.2f} in-place")

    st.markdown('<div class="section-label">Acquisition Read</div>', unsafe_allow_html=True)
    st.info(f"{valuation.recommendation}. Indicated value is {_money(valuation.blended_value)} versus asking price of {_money(valuation.asking_price)}.")

    tabs = st.tabs(["Model", "Rent Roll", "Market", "Comps"])
    with tabs[0]:
        _render_model_tab(valuation)
    with tabs[1]:
        _render_rent_roll(tenants)
    with tabs[2]:
        _render_market_tab(market, valuation)
    with tabs[3]:
        _render_comps_tab(comps)


def _render_model_tab(valuation: ValuationResult) -> None:
    bridge = pd.DataFrame(
        [
            {"Method": "Cap-rate value", "Value": valuation.cap_rate_value},
            {"Method": "Sales comp value", "Value": valuation.sales_comp_value},
            {"Method": "DCF value", "Value": valuation.dcf_value},
            {"Method": "Blended value", "Value": valuation.blended_value},
            {"Method": "Asking price", "Value": valuation.asking_price},
        ]
    )
    chart = (
        alt.Chart(bridge)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Method:N", sort=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Value:Q", axis=alt.Axis(format="$~s")),
            color=alt.condition(
                alt.datum.Method == "Blended value",
                alt.value("#126C67"),
                alt.value("#8FA8A0"),
            ),
            tooltip=["Method:N", alt.Tooltip("Value:Q", format="$,.0f")],
        )
        .properties(height=245)
    )
    st.altair_chart(chart, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown('<div class="section-label">Risks</div>', unsafe_allow_html=True)
        for item in valuation.risks:
            st.write(f"- {item}")
    with right:
        st.markdown('<div class="section-label">Opportunities</div>', unsafe_allow_html=True)
        for item in valuation.opportunities:
            st.write(f"- {item}")


def _render_rent_roll(tenants: pd.DataFrame) -> None:
    display = tenants.copy()
    display["annual_rent"] = display["area_sf"] * display["rent_psf"]
    st.dataframe(
        display[
            ["tenant", "category", "suite", "area_sf", "rent_psf", "annual_rent", "lease_end", "credit", "sales_psf"]
        ],
        column_config={
            "tenant": "Tenant",
            "category": "Category",
            "suite": "Suite",
            "area_sf": st.column_config.NumberColumn("Area", format="%d sf"),
            "rent_psf": st.column_config.NumberColumn("Rent / SF", format="$%.2f"),
            "annual_rent": st.column_config.NumberColumn("Annual Rent", format="$%d"),
            "lease_end": st.column_config.DateColumn("Lease End"),
            "credit": "Credit",
            "sales_psf": st.column_config.NumberColumn("Sales / SF", format="$%d"),
        },
        hide_index=True,
        width="stretch",
    )


def _render_market_tab(market: pd.Series, valuation: ValuationResult) -> None:
    cols = st.columns(3)
    cols[0].metric("Population", f"{int(market['population']):,}", f"{market['trade_area_miles']} mi")
    cols[1].metric("Median Income", _money(float(market["median_income"])), f"{market['growth_5yr_pct']:.1f}% growth")
    cols[2].metric("Traffic Count", f"{int(market['traffic_count']):,}", f"Age {market['median_age']:.1f}")
    st.markdown(
        f"""
        <p class="note">
        The model converts income, growth, traffic, occupancy, rollover, and comparable cap rates into a
        risk-adjusted cap rate of <strong>{valuation.cap_rate:.2%}</strong>.
        </p>
        """,
        unsafe_allow_html=True,
    )


def _render_comps_tab(comps: pd.DataFrame) -> None:
    st.dataframe(
        comps[["comp_name", "distance_miles", "sale_price", "cap_rate", "price_psf", "sale_date"]],
        column_config={
            "comp_name": "Comp",
            "distance_miles": st.column_config.NumberColumn("Distance", format="%.1f mi"),
            "sale_price": st.column_config.NumberColumn("Sale Price", format="$%d"),
            "cap_rate": st.column_config.NumberColumn("Cap Rate", format="%.2%"),
            "price_psf": st.column_config.NumberColumn("Price / SF", format="$%d"),
            "sale_date": st.column_config.DateColumn("Sale Date"),
        },
        hide_index=True,
        width="stretch",
    )


def _money(value: float) -> str:
    return f"${value:,.0f}"


if __name__ == "__main__":
    main()
