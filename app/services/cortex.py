from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.data_access import get_snowflake_config, get_snowpark_session
from app.observability import ai_track, capture_exception, start_agent_transaction, start_span
from app.services.valuation import ValuationResult, answer_locally


SYSTEM_PROMPT = """
You are an institutional commercial real estate acquisition analyst.
Use the supplied property, rent roll, trade area, sales comps, and valuation math.
Be concise, cite the specific metric behind each conclusion, and do not invent facts.
If data is missing, say what diligence is still required.
"""


@ai_track("RetailScope AI answer")
def answer_agent(
    question: str,
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
) -> str:
    transaction_data = {
        "property_id": str(property_row["property_id"]),
        "question_length": len(question),
        "tenant_count": int(len(tenants)),
        "comp_count": int(len(comps)),
    }
    with start_agent_transaction("retailscope_ai.answer", transaction_data):
        with start_span("ai.prompt.build", "Build appraisal prompt"):
            prompt = _build_prompt(question, property_row, tenants, market, comps, valuation)

        cortex_answer = _try_cortex_complete(prompt)
        if cortex_answer:
            return cortex_answer

        with start_span(
            "ai.fallback",
            "Deterministic local appraisal answer",
            {"reason": "cortex_unavailable_or_unconfigured"},
        ):
            return answer_locally(question, property_row, tenants, market, comps, valuation)


def _try_cortex_complete(prompt: str) -> str | None:
    cfg = get_snowflake_config()
    if not cfg:
        return None

    model = cfg.get("cortex_model", "claude-sonnet-5")
    try:
        from snowflake.snowpark.functions import ai_complete

        session = get_snowpark_session(cfg)
        full_prompt = f"{SYSTEM_PROMPT}\n\nAppraisal context:\n{prompt}"
        with start_span(
            "ai.chat_completions.create.snowflake_cortex",
            "Snowflake Cortex ai_complete",
            {"model": model, "provider": "snowflake_cortex"},
        ):
            df = session.range(1).select(
                ai_complete(model=model, prompt=full_prompt).alias("response")
            )
            response_raw = df.collect()[0][0]
            return _extract_cortex_text(response_raw)
    except Exception as exc:
        capture_exception(exc)
        st.caption(f"Cortex unavailable; using deterministic local analyst. Details: {exc}")
        return None


def _extract_cortex_text(response_raw: object) -> str:
    if response_raw is None:
        return ""
    if isinstance(response_raw, str):
        try:
            parsed = json.loads(response_raw)
        except json.JSONDecodeError:
            return response_raw
    else:
        parsed = response_raw

    if isinstance(parsed, dict):
        choices = parsed.get("choices")
        if choices and isinstance(choices, list):
            message = choices[0].get("messages") or choices[0].get("message") or choices[0].get("text")
            if isinstance(message, dict):
                return str(message.get("content", ""))
            return str(message or "")
        return str(parsed.get("response") or parsed.get("text") or parsed)
    return str(parsed)


def _build_prompt(
    question: str,
    property_row: pd.Series,
    tenants: pd.DataFrame,
    market: pd.Series,
    comps: pd.DataFrame,
    valuation: ValuationResult,
) -> str:
    payload = {
        "question": question,
        "property": _series_to_dict(property_row),
        "tenants": _records(tenants),
        "market": _series_to_dict(market),
        "sales_comps": _records(comps),
        "valuation": valuation.__dict__,
    }
    return json.dumps(payload, default=str, indent=2)


def _records(df: pd.DataFrame) -> list[dict]:
    return [
        {key: _json_safe(value) for key, value in record.items()}
        for record in df.to_dict(orient="records")
    ]


def _series_to_dict(series: pd.Series) -> dict:
    return {key: _json_safe(value) for key, value in series.to_dict().items()}


def _json_safe(value):
    if hasattr(value, "item"):
        return value.item()
    return value
