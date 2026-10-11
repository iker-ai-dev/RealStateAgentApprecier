from __future__ import annotations
from app.data_access import get_snowflake_config, get_snowpark_session
from app.observability import start_agent_transaction, start_span
from app.acquisition_policy import render_research_answer
from app.services.topic_router import route_topic


def answer_agent(question, property_row, tenants, market, comps, valuation):
    with start_agent_transaction("retailscope_ai.answer"):
        cfg = get_snowflake_config()
        model = cfg.get("cortex_model", "")
        session = None
        if isinstance(model, str) and model.strip():
            try:
                session = get_snowpark_session(cfg)
            except Exception:
                session = None
        topic, provider = route_topic(question, session, model)
        with start_span("ai.fallback", "Deterministic local appraisal answer"):
            return render_research_answer(topic, property_row, tenants, market, comps, valuation, provider=provider)
