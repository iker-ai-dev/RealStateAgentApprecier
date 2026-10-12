from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
import tomllib
from typing import Any

import pandas as pd
import streamlit as st


DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class PropertyDataset:
    properties: pd.DataFrame
    tenants: pd.DataFrame
    markets: pd.DataFrame
    sales_comps: pd.DataFrame
    source: str


def load_dataset(*, require_snowflake: bool = False) -> PropertyDataset:
    """Load Snowflake data when configured, otherwise use bundled sample data."""
    public_demo = os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true"
    if public_demo and require_snowflake:
        raise RuntimeError("Public demo cannot access Snowflake data.")
    if not public_demo:
        snowflake_data = _try_load_snowflake(require_snowflake=require_snowflake)
        if snowflake_data is not None:
            return snowflake_data

    return PropertyDataset(
        properties=pd.read_csv(DATA_DIR / "properties.csv"),
        tenants=pd.read_csv(DATA_DIR / "tenants.csv", parse_dates=["lease_end"]),
        markets=pd.read_csv(DATA_DIR / "markets.csv"),
        sales_comps=pd.read_csv(DATA_DIR / "sales_comps.csv", parse_dates=["sale_date"]),
        source="Sample CSV data",
    )


def _try_load_snowflake(*, require_snowflake: bool = False) -> PropertyDataset | None:
    cfg = get_snowflake_config()
    configured = any(cfg.get(key) for key in (
        "account", "user", "password", "role", "warehouse", "database", "schema",
        "authenticator", "private_key_file",
    ))
    # Streamlit in Snowflake supplies an active session without local credentials.
    hosted = "_snowflake" in sys.modules
    if not configured and not hosted:
        try:
            hosted = importlib.util.find_spec("_snowflake") is not None
        except (ImportError, ValueError):
            hosted = False
    if not configured and not hosted:
        if require_snowflake:
            raise RuntimeError("Snowflake is not configured. Demo data was not substituted.")
        return None

    try:
        session = get_snowpark_session(cfg)
        tables = {
            "properties": cfg.get("property_table", "SHOPPING_CENTER_PROPERTIES"),
            "tenants": cfg.get("tenant_table", "SHOPPING_CENTER_TENANTS"),
            "markets": cfg.get("market_table", "SHOPPING_CENTER_MARKETS"),
            "sales_comps": cfg.get("sales_comp_table", "SHOPPING_CENTER_SALES_COMPS"),
        }

        data = {
            name: _normalize_columns(session.table(table_name).limit(10001).to_pandas())
            for name, table_name in tables.items()
        }

        if any(len(frame) > 10000 for frame in data.values()):
            raise ValueError("Dataset exceeds bounded demo load.")
        if "lease_end" in data["tenants"].columns:
            data["tenants"]["lease_end"] = pd.to_datetime(data["tenants"]["lease_end"])
        if "sale_date" in data["sales_comps"].columns:
            data["sales_comps"]["sale_date"] = pd.to_datetime(data["sales_comps"]["sale_date"])

        return PropertyDataset(
            properties=data["properties"],
            tenants=data["tenants"],
            markets=data["markets"],
            sales_comps=data["sales_comps"],
            source="Snowflake",
        )
    except Exception as exc:
        if require_snowflake:
            raise RuntimeError(
                "Snowflake data could not be loaded. Check connection settings, table access, "
                "and dataset size (maximum 10,000 rows per table). Demo data was not substituted."
            ) from None
        _warn("Snowflake data could not be loaded; explicitly using synthetic sample data.")
        return None


def get_snowflake_config() -> dict[str, Any]:
    if os.getenv("RETAILSCOPE_PUBLIC_DEMO") == "true":
        return {}
    local_cfg = {}
    connection_name = os.getenv("SNOWFLAKE_CONNECTION_NAME")
    if connection_name:
        config_path = Path.home() / ".snowflake" / "config.toml"
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        local_cfg = dict(config.get("connections", {}).get(connection_name, {}))
        if not local_cfg:
            raise ValueError("The named Snowflake connection was not found.")
    try:
        app_cfg = dict(st.secrets.get("snowflake", {}))
    except Exception:
        app_cfg = {}

    try:
        connection_cfg = dict(st.secrets.get("connections", {}).get("snowflake", {}))
    except Exception:
        connection_cfg = {}

    env_cfg = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
        "property_table": os.getenv("SNOWFLAKE_PROPERTY_TABLE"),
        "tenant_table": os.getenv("SNOWFLAKE_TENANT_TABLE"),
        "market_table": os.getenv("SNOWFLAKE_MARKET_TABLE"),
        "sales_comp_table": os.getenv("SNOWFLAKE_SALES_COMP_TABLE"),
        "cortex_model": os.getenv("SNOWFLAKE_CORTEX_MODEL"),
        "authenticator": os.getenv("SNOWFLAKE_AUTHENTICATOR"),
        "private_key_file": os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"),
    }
    env_cfg = {key: value for key, value in env_cfg.items() if value}

    return {**local_cfg, **connection_cfg, **app_cfg, **env_cfg}


def get_snowpark_session(cfg: dict[str, Any] | None = None):
    cfg = cfg or get_snowflake_config()
    try:
        from snowflake.snowpark.context import get_active_session

        return get_active_session()
    except Exception:
        from snowflake.snowpark import Session

        connection_parameters = {
            key: cfg[key]
            for key in ("account", "user", "password", "role", "warehouse", "database", "schema",
                        "authenticator", "private_key_file")
            if cfg.get(key)
        }
        connection_parameters.update(login_timeout=20, network_timeout=30, socket_timeout=30)
        return Session.builder.configs(connection_parameters).create()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(column).lower() for column in df.columns]
    return df


def _warn(message: str) -> None:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is not None:
            st.warning(message)
    except Exception:
        pass
