"""Explicit, bounded Nimble discovery; no network or secret reads at import time."""
import importlib
import os
from datetime import datetime, timezone

import streamlit as st

from nimble_mcp import NimbleMCP
from app.observability import start_span, start_agent_transaction


def _nimble_key():
    if os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true":
        return ""
    try:
        binding = importlib.import_module("_snowflake")
    except ImportError:
        try:
            value = st.secrets.get("NIMBLE_API_KEY", "")
        except Exception:
            value = ""
        return value or os.getenv("NIMBLE_API_KEY", "")
    try:
        return binding.get_generic_secret_string("NIMBLE_API_KEY")
    except Exception:
        return ""  # Warehouse never falls back to personal local credentials.


def render_research(market):
    if os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true":
        return
    with st.expander("Live market discovery with Nimble"):
        st.caption("Search public evidence first. One search may consume Nimble credits. "
                   "Search results are discovery leads, not verified rent rolls or income forecasts.")
        scope = st.radio("Research scope", ["Selected market", "Growing U.S. markets"], horizontal=True)
        query = (market + " open-air retail strip center asking rents vacancy population household income sources"
                 if scope == "Selected market" else
                 "United States fastest growing counties official population projections 2030 2035 Census household income retail demand")
        question = st.text_input("Public research query", value=query, key="nimble_query_" + scope + market, max_chars=4000)
        if st.button("Search public evidence", key="nimble_search"):
            key = _nimble_key()
            if not key:
                st.info("Live research is not configured in this workspace.")
            else:
                try:
                    with start_agent_transaction("retailscope_ai.research"), start_span("mcp.search", "nimble.search"):
                        with NimbleMCP(key, max_search_calls=1) as client:
                            result = client.search(question)
                    st.session_state.nimble_evidence = {"query": question, "market": market,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "DISCOVERY_ONLY", "result": result}
                except Exception:
                    st.session_state.pop("nimble_evidence", None)
                    st.warning("Public research could not complete. Check integration configuration and retry explicitly.")
        evidence = st.session_state.get("nimble_evidence")
        if evidence and evidence["market"] == market:
            st.json(evidence)
            st.caption("These leads do not change the valuation inputs. Open and verify each material source before underwriting.")


def render_listing_search():
    """One user-triggered discovery call; no credential values appear in the UI."""
    if os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true":
        return
    from app.listing_discovery import discover_listings
    with st.expander("Find fresh public listing leads"):
        st.caption("Search Crexi, LoopNet and public broker sites. One search may consume Nimble credits. "
                   "Prices, occupancy and income require page/document verification before a lead qualifies.")
        query = st.text_input("Listing search", value="US open-air strip shopping center for sale value add vacancy occupancy price 1 million 10 million", max_chars=4000)
        if st.button("Search current listings", key="listing_search"):
            key = _nimble_key()
            if not key:
                st.info("Live listing research is not configured in this workspace.")
                return
            try:
                run = discover_listings(key, query)
                st.session_state.listing_leads = run["leads"]
                st.session_state.listing_discovery = {"query": query, "status": run["status"]}
                st.success("Public leads retrieved. Unknown price/occupancy remains unknown; target fit is not established.")
            except Exception:
                st.session_state.pop("listing_leads", None)
                st.warning("Listing discovery could not complete. Retry explicitly after checking integration configuration.")
