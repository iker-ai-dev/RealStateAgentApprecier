# RetailScope AI

RetailScope AI is a Streamlit app for exploring acquisition value on open-air commercial shopping centers. It pairs a chat UI with a right-side preview panel that updates with the current property's rent roll, market context, valuation model, and acquisition read.

The app runs locally with bundled sample data and can be pointed at Snowflake tables plus Snowflake Cortex when credentials are available.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## MCP Server

RetailScope AI can also run as a local stdio MCP server for coding agents:

```bash
uvx --from git+https://github.com/iker-ai-dev/RealStateAgentApprecier.git retailscope-mcp
```

Agent install commands:

```bash
codex mcp add retailscope-ai -- uvx --from git+https://github.com/iker-ai-dev/RealStateAgentApprecier.git retailscope-mcp
claude mcp add retailscope-ai -- uvx --from git+https://github.com/iker-ai-dev/RealStateAgentApprecier.git retailscope-mcp
opencode mcp add retailscope-ai -- uvx --from git+https://github.com/iker-ai-dev/RealStateAgentApprecier.git retailscope-mcp
```

The server exposes:

- `list_shopping_centers`
- `get_shopping_center_context`
- `appraise_shopping_center`
- `ask_retailscope`

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
cortex_model = "claude-sonnet-5"
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
SNOWFLAKE_CORTEX_MODEL=claude-sonnet-5
```

## Sentry Observability

The app initializes Sentry when `SENTRY_DSN` is set or when secrets include:

```toml
[sentry]
dsn = "https://..."
environment = "local"
traces_sample_rate = 0.2
profiles_sample_rate = 0.0
```

Sentry spans are emitted for dataset loading, valuation, prompt construction, Cortex calls, and local fallback responses. Raw prompts, tenant rows, secrets, and DSNs are filtered before events are sent.

## Deployment Options

- **Streamlit in Snowflake**: best fit when the app should run next to governed Snowflake data and use the active Snowpark session in Snowsight.
- **Streamlit Community Cloud**: good for a fast hosted demo from GitHub; add Snowflake and Sentry values in app secrets.
- **Snowpark Container Services or another container host**: best for production control, private networking, CI/CD, and release management.
- **Render/Fly.io/Railway**: simple external hosting if Snowflake is accessed over the network with secrets.

This repository includes a `Dockerfile` and `render.yaml` for container-based deployment.

For fast Streamlit in Snowflake deployment, use `snowflake_streamlit_app.py` as a single-file entrypoint. It uses active Snowpark session data when the expected tables exist and falls back to embedded demo data otherwise.

## CI/CD Deployment

`.github/workflows/deploy-snowflake.yml` runs compile and data-path smoke tests on pull requests. When changes land on `main`, it uses Snowflake CLI to deploy `snowflake_streamlit_app.py` with `snow streamlit deploy`.

Configure these GitHub Actions secrets before relying on automatic deployment:

- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_USER`
- `SNOWFLAKE_PRIVATE_KEY` (PKCS#8 PEM registered on the Snowflake user), or `SNOWFLAKE_PASSWORD`
- `SNOWFLAKE_ROLE`
- `SNOWFLAKE_WAREHOUSE`
- `SNOWFLAKE_DATABASE`
- `SNOWFLAKE_SCHEMA`

The Snowflake project definition lives in `snowflake.yml`.

For unattended deployment, prefer a dedicated Snowflake service user with key-pair authentication. Store its private key only in GitHub Actions secrets. The workflow writes the key to a restricted temporary file and removes it after each command. Password authentication remains available for accounts that allow it without an interactive MFA challenge.

MCP installation itself requires no credentials and initially uses bundled demo data. To query your Snowflake tables, configure the `SNOWFLAKE_*` connection variables on the machine running the MCP server. GitHub deployment secrets are not passed to installed MCP clients.

## What The Agent Does

- Answers acquisition questions against the selected center.
- Shows the assumptions and intermediate math in the preview panel.
- Estimates NOI, cap-rate value, price-per-square-foot value, DCF value, and a blended value.
- Flags risks such as lease rollover, weak occupancy, low income density, or rent variance.
- Uses Snowflake Cortex for narrative responses when configured, with deterministic local fallback when not.

This is a decision-support prototype, not a certified appraisal.
