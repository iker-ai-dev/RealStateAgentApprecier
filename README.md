# RetailScope AI

## Live Workspace

The public application at https://retailscope-ai-demo.onrender.com is a React/TypeScript
workspace backed by FastAPI, live Nimble discovery, Snowflake Cortex inference,
and durable Snowflake workspace records. It does not fall back to sample data.
The older Streamlit application below remains a separate deployment/reference.

- **Chat:** choose a US city, purchase budget, and investment strategy, then send
  the generated research prompt. Ask follow-up questions about the same evidence.
- **Background agents:** save up to five searches per browser workspace. Pause,
  resume, or run a search immediately. Results arrive in a durable lead inbox.
- **Lead details:** the same source-backed metric view is used in chat and the
  inbox. Price, area, cap rate, occupancy, NOI, asking rent, population, and median
  household income are shown only when explicitly extractable. Missing,
  ambiguous, or multi-property values stay unknown. These are source assertions,
  not verified appraisals or guaranteed budget matches.

Background searches are dispatched by `.github/workflows/scheduled-searches.yml`
at 00:17, 06:17, 12:17, and 18:17 UTC. GitHub schedules may be delayed; this is not
an exact-time SLA. The dispatcher wakes Render and drains up to 20 due searches,
one at a time, without needing an open browser or an always-on Render worker.
Only an authenticated internal request can dispatch work across owners. A durable
conditional claim prevents the same scheduled slot being charged twice; an
interrupted run is not automatically retried. Failed and partial runs remain visible.
Pause stops future scheduled runs, not an already executing provider request.

The workflow requires the GitHub Actions secret `RETAILSCOPE_SCHEDULER_SECRET`.
The backend uses an explicit secret of that name when configured, otherwise a
domain-separated HMAC-SHA256 of its persistent session signing key with the label
`retailscope:background-dispatch:v1`. Only the derived dispatch credential belongs
in GitHub; the session signing key must remain on the backend. Rotating the session
key requires rotating the derived dispatch secret too. Never place either in Vite
environment variables or source code. The dispatcher receives run status only,
not private workspace data or provider credentials.

Runs share the existing global daily allowance (default 120 research/chat calls),
owner/IP limits, and bounded provider searches. Nimble usage is billed to the
configured Nimble account; inference and storage are billed to Snowflake. A saved
agent permits recurring provider usage until paused or its browser session expires
(at most 30 days). Anonymous enrollment has a durable global cap of 20 unexpired
subscriptions. The current identity is a
signed browser session, not a cross-device account login: returning in the same
browser restores the workspace while its session remains valid (30 days).
Provision `SNOWFLAKE_AGENT_SLOTS_SQL` from `app/workspace_store.py` once in a
serialized admin migration before enabling enrollment. Missing slots fail closed;
the application does not grant itself DDL privileges or silently bypass the cap.

Local live development: build `web` with `npm ci && npm run build`, then run
`uvicorn app.web_api:create_app --factory --port 8766`. Use real provider settings
for discovery/inference; SQLite is supported locally. Production requires Snowflake.
The live MCP endpoint requires a workspace token created through `/landing`.

## Legacy Streamlit Reference

The following describes the original hackathon Streamlit build, not the live
React workspace. See `INTEGRATION_STATUS.txt` for that build's limits.

[User manual](https://retailscope-user-manual.starryharp64.chatgpt.site) covers deal discovery, center analysis, MCP installation, public research, Snowflake connections, deployment, Sentry, and troubleshooting. The Sites manual currently has owner-private access.

[Demo video](https://www.loom.com/share/a2f8d24da7614c4692d73b44859062b6) shows the local hackathon build.

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

## MCP Server

The MCP install page is available at `/landing` (locally, `http://localhost:8501/landing`). It includes copyable commands for Codex, Claude Code, OpenCode, and a generic MCP JSON configuration.

RetailScope AI can also run as a local stdio MCP server for coding agents:

```bash
uvx --from git+https://github.com/iker-ai-dev/RealStateAgentApprecier.git retailscope-mcp
```

Agent install commands:

```bash
codex mcp add retailscope-ai --url https://retailscope-ai-demo.onrender.com/mcp
claude mcp add --transport http retailscope-ai https://retailscope-ai-demo.onrender.com/mcp
```

The public endpoint uses stateless Streamable HTTP and bundled synthetic sample
data only. It never reads Snowflake connection settings or private tables, even
if credentials are accidentally present. Answers are deterministic, not live
LLM inference. Local stdio remains available for authenticated private data.
OpenCode's remote JSON configuration is provided on `/landing`.

The discovery workspace runs three bounded background workers over saved public
listing snapshots, followed by screening and source-order summarization. Worker
states, counts and snapshot vintage are visible. Refreshing a scan does not fetch
fresh listings or make an LLM call. Unknown facts remain unknown, and public leads
are kept separate from the synthetic centers used for illustrative valuation.

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
traces_sample_rate = 0.2
```

Sentry records MCP workflows, valuation calculations, research answers, and cached background discovery. Workflow traces contain timed steps and completion status. Structured lifecycle logs and sanitized operational errors link to their trace. Prompts, responses, property records, credentials, raw exception messages, profiles, and session capture are excluded; outbound payloads are reconstructed from an allowlist.

On Render, configure `SENTRY_DSN`, `RETAILSCOPE_RUNTIME=container`, and `SENTRY_TRACES_SAMPLE_RATE=1.0` for complete demo tracing. The environment identifies Render demo traffic separately from local and Snowflake traffic. A supported deployment commit SHA identifies the release. Snowflake reads its managed `SENTRY_DSN` secret alias. Configuration is optional; verify delivery by exercising a workflow and finding its trace and lifecycle logs in Sentry.

## Deployment Options

- **Streamlit in Snowflake**: best fit when the app should run next to governed Snowflake data and use the active Snowpark session in Snowsight.
- **Streamlit Community Cloud**: good for a fast hosted demo from GitHub; add Snowflake and Sentry values in app secrets.
- **Snowpark Container Services or another container host**: best for production control, private networking, CI/CD, and release management.
- **Render/Fly.io/Railway**: simple external hosting if Snowflake is accessed over the network with secrets.

This repository includes a `Dockerfile` and `render.yaml` for container-based deployment.

The public Render container supervises Streamlit and the demo-only MCP server
behind nginx on `$PORT` (default 10000). `/mcp` routes to MCP; other routes and
WebSockets reach Streamlit. `/healthz` is the public health check. MCP requests
have a 64 KiB body limit, IP rate limiting, and Host/Origin allowlists. The image
excludes local secrets, Git metadata, and generated artifacts. Render Free may
cold-start after inactivity. Authenticated private datasets must not be exposed
through this anonymous demo service.

For Streamlit in Snowflake deployment, use `snowflake_streamlit_app.py` as the entrypoint and upload the complete repository modules. It uses active Snowpark session data when the expected tables exist and falls back to embedded demo data otherwise.

## CI/CD Deployment

`.github/workflows/deploy-snowflake.yml` runs compile and data-path smoke tests on pull requests. When changes land on `main`, it uses Snowflake CLI to deploy `snowflake_streamlit_app.py` with `snow streamlit deploy`.

The same tested-main workflow triggers the public Render app/MCP deployment
through its service-specific `RENDER_DEPLOY_HOOK_URL` repository secret, pinned
to the tested commit SHA. Render's independent auto-deploy is disabled so the
test-gated GitHub pipeline owns releases. Deploy hooks contain credentials and
must never be committed or shown in the manual.

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

The prepared service username is `RETAILSCOPE_DEPLOY`. An administrator must run `deploy/bootstrap_snowflake_user.sql` in Snowflake to create it and register its public key. The private key is stored separately from the repository. Confirm the existing warehouse and configure `SNOWFLAKE_WAREHOUSE` in GitHub before enabling deployment. The script reuses the existing deployment role; it does not transfer app ownership or create compute.

MCP installation itself requires no credentials and initially uses bundled demo data. To query your Snowflake tables, configure the `SNOWFLAKE_*` connection variables on the machine running the MCP server. GitHub deployment secrets are not passed to installed MCP clients.

For local key-pair access, set `SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT` and `SNOWFLAKE_PRIVATE_KEY_FILE` to the private key path along with your connection settings. Configured MCP clients fail with a loading error instead of receiving synthetic data when Snowflake is unavailable or a table exceeds the bounded load. `RETAILSCOPE_REQUIRE_SNOWFLAKE=true` explicitly disables demo fallback for MCP.

## What The Agent Does

- Answers acquisition questions against the selected center.
- Shows the assumptions and intermediate math in the preview panel.
- Estimates NOI, cap-rate value, price-per-square-foot value, DCF value, and a blended value.
- Flags risks such as lease rollover, weak occupancy, missing income/density evidence, or rent variance.
- Uses bounded structured Snowflake Cortex topic routing when available, with host-rendered research answers and a labeled deterministic fallback.

This is a decision-support prototype, not a certified appraisal.
