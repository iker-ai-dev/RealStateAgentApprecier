from __future__ import annotations

from dataclasses import asdict
import os
from typing import Any

import pandas as pd

from app.data_access import PropertyDataset, get_snowflake_config, load_dataset
from app.observability import set_agent_context, start_span
from app.services.cortex import answer_agent
from app.services.valuation import ValuationResult, build_valuation


def list_properties() -> list[dict[str, Any]]:
    dataset = _load_agent_dataset()
    rows = []
    for row in dataset.properties.itertuples():
        rows.append(
            {
                "property_id": row.property_id,
                "name": row.name,
                "city": row.city,
                "state": row.state,
                "gla_sf": int(row.gla_sf),
                "occupancy_pct": float(row.occupancy_pct),
                "asking_price": float(row.asking_price),
                "source": dataset.source,
            }
        )
    return rows


def get_property_context(property_id: str) -> dict[str, Any]:
    dataset, property_row, tenants, market, comps, valuation = load_property_analysis(property_id)
    return {
        "property": _series_to_dict(property_row),
        "tenants": _records(tenants),
        "market": _series_to_dict(market),
        "sales_comps": _records(comps),
        "valuation": valuation_to_dict(valuation),
        "source": dataset.source,
    }


def appraise_property(property_id: str) -> dict[str, Any]:
    dataset, property_row, tenants, market, comps, valuation = load_property_analysis(property_id)
    return {
        "property_id": valuation.property_id,
        "property_name": str(property_row["name"]),
        "source": dataset.source,
        "valuation": valuation_to_dict(valuation),
        "tenant_count": int(len(tenants)),
        "comp_count": int(len(comps)),
    }


def ask_property_agent(property_id: str, question: str) -> dict[str, Any]:
    dataset, property_row, tenants, market, comps, valuation = load_property_analysis(property_id)
    set_agent_context(str(property_row["property_id"]), str(property_row["name"]), dataset.source)
    answer = answer_agent(question, property_row, tenants, market, comps, valuation)
    return {
        "property_id": property_id,
        "property_name": str(property_row["name"]),
        "question": question,
        "answer": answer,
        "valuation": valuation_to_dict(valuation),
        "source": dataset.source,
    }


def load_property_analysis(
    property_id: str,
) -> tuple[PropertyDataset, pd.Series, pd.DataFrame, pd.Series, pd.DataFrame, ValuationResult]:
    dataset = _load_agent_dataset()
    normalized_id = property_id.strip()
    matches = dataset.properties.loc[dataset.properties["property_id"] == normalized_id]
    if matches.empty:
        available = ", ".join(str(value) for value in dataset.properties["property_id"].tolist())
        raise ValueError(f"Unknown property_id '{property_id}'. Available property IDs: {available}")

    property_row = matches.iloc[0]
    tenants = dataset.tenants.loc[dataset.tenants["property_id"] == normalized_id].copy()
    market = dataset.markets.loc[dataset.markets["property_id"] == normalized_id].iloc[0]
    comps = dataset.sales_comps.loc[dataset.sales_comps["property_id"] == normalized_id].copy()

    with start_span(
        "app.valuation",
        "Build commercial real estate valuation",
        {"property_id": normalized_id},
    ):
        valuation = build_valuation(property_row, tenants, market, comps)

    return dataset, property_row, tenants, market, comps, valuation


def valuation_to_dict(valuation: ValuationResult) -> dict[str, Any]:
    return _json_safe(asdict(valuation))


def _load_agent_dataset() -> PropertyDataset:
    cfg = get_snowflake_config()
    require_live = bool(cfg.get("account") or cfg.get("user")) or os.getenv(
        "RETAILSCOPE_REQUIRE_SNOWFLAKE", ""
    ).lower() in {"1", "true", "yes"}
    with start_span("db.load", "Load shopping center dataset"):
        return load_dataset(require_snowflake=require_live)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(record) for record in df.to_dict(orient="records")]


def _series_to_dict(series: pd.Series) -> dict[str, Any]:
    return _json_safe(series.to_dict())


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
