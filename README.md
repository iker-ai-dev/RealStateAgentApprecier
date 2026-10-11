# Integration review status

This branch is a draft integration. See [INTEGRATION_STATUS.txt](INTEGRATION_STATUS.txt) for known limits and target-runtime checks. No investment return engine or completed appraisal is claimed. Cortex routes research topics; host code renders financial facts. Public sample records and legacy heuristics remain unverified. Upload all repository modules for Snowflake deployment.

# RetailScope AI

RetailScope AI is a Streamlit app for exploring acquisition value on open-air commercial shopping centers. It pairs a chat UI with a right-side preview panel, bounded public-evidence discovery, illustrative valuation and a needs-diligence gate.

The default **Find deals** screen filters public listing leads by market, advertised price, reported vacancy, source and sourced income coverage. The initial seven records are broker assertions with unknown current availability, including one historical listing requiring an availability recheck. They are research leads, not qualified investments. Occupancy basis, vacancy hypotheses, counterevidence and missing documents are visible. County demographics are shown separately from property trade areas; future income is unknown.

**Center analysis** preserves the chat and valuation tabs for the separate synthetic demo dataset. Selecting a listing never substitutes an unrelated demo rent roll. Current public evidence does not establish 20%+ equity IRR or the separate 6–7× equity-return goal across 5-, 7- and 10-year holds.

The app runs locally with bundled sample data and can be pointed at Snowflake tables plus Snowflake Cortex when credentials are available.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Snowflake Setup

For local Snowflake access, create `.streamlit/secrets.toml`. The app supports the Snowflake tutorial's `[connections.snowflake]` convention plus app-specific table/model settings under `[snowflake]`:

```toml
[connections.snowflake]
account = "..."
user = "..."
password = "..."
role = "..."
warehouse = "..."
database = "..."
schema = "..."

[snowflake]
property_table = "SHOPPING_CENTER_PROPERTIES"
tenant_table = "SHOPPING_CENTER_TENANTS"
market_table = "SHOPPING_CENTER_MARKETS"
sales_comp_table = "SHOPPING_CENTER_SALES_COMPS"
cortex_model = "" # Set only to the model provisioned by the hackathon.
```

The app expects these logical fields:

- `SHOPPING_CENTER_PROPERTIES`: `property_id`, `name`, `address`, `city`, `state`, `gla_sf`, `year_built`, `occupancy_pct`, `asking_price`, `notes`
- `SHOPPING_CENTER_TENANTS`: `property_id`, `tenant`, `category`, `suite`, `area_sf`, `rent_psf`, `lease_end`, `credit`, `sales_psf`
- `SHOPPING_CENTER_MARKETS`: `property_id`, `trade_area_miles`, `population`, `median_income`, `growth_5yr_pct`, `traffic_count`, `median_age`
- `SHOPPING_CENTER_SALES_COMPS`: `property_id`, `comp_name`, `distance_miles`, `sale_price`, `cap_rate`, `price_psf`, `sale_date`

When running inside Streamlit in Snowflake, the app attempts to use the active Snowpark session.

Hosted deployments can also use environment variables instead of Streamlit secrets:

```bash
SNOWFLAKE_ACCOUNT=...
SNOWFLAKE_USER=...
SNOWFLAKE_PASSWORD=...
SNOWFLAKE_ROLE=...
SNOWFLAKE_WAREHOUSE=...
SNOWFLAKE_DATABASE=...
SNOWFLAKE_SCHEMA=...
SNOWFLAKE_CORTEX_MODEL=
```

The model setting ships blank. No model is automatically chosen, and no provider request is made until a hackathon-provided Cortex model is configured. Blank configuration uses labeled deterministic research answers. Personal AI API keys are not used or required.

## Sentry Observability

The app initializes Sentry when `SENTRY_DSN` is set or when secrets include:

```toml
[sentry]
dsn = "https://..."
environment = "local"
traces_sample_rate = 0.2
profiles_sample_rate = 0.0
```

Sentry uses static operation names, generated trace IDs, timing and fixed status values. SDK automatic AI capture, logs, profiles, sessions, request bodies and local variables are disabled. Both error events and transaction payloads are reconstructed from an allowlist. Configuration is optional; initialization does not prove remote delivery. The privacy wrapper is tested offline with real SDK2.67.1 and2.71.0; target-account delivery is unverified.

## Deployment Options

- **Streamlit in Snowflake**: best fit when the app should run next to governed Snowflake data and use the active Snowpark session in Snowsight.
- **Streamlit Community Cloud**: good for a fast hosted demo from GitHub; add Snowflake and Sentry values in app secrets.
- **Snowpark Container Services or another container host**: best for production control, private networking, CI/CD, and release management.
- **Render/Fly.io/Railway**: simple external hosting if Snowflake is accessed over the network with secrets.

This repository includes a `Dockerfile` and `render.yaml` for container-based deployment.

For Streamlit in Snowflake deployment, use `snowflake_streamlit_app.py` as the entrypoint and upload the complete repository modules. It uses active Snowpark session data when the expected tables exist and falls back to embedded demo data otherwise.

## What The Agent Does

- Answers acquisition questions against the selected center.
- Shows the assumptions and intermediate math in the preview panel.
- Estimates NOI, cap-rate value, price-per-square-foot value, DCF value, and a blended value.
- Flags risks such as lease rollover, weak occupancy, missing income/density evidence, or rent variance.
- Uses bounded structured Snowflake Cortex topic routing when available, with host-rendered research answers and a labeled deterministic fallback.

This is a decision-support prototype, not a certified appraisal.
