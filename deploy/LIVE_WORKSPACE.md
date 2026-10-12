# Live workspace deployment

The primary application is the React/TypeScript frontend in `web/`, served by
`app.web_api` through Nginx. Python retains the financial engine, LangGraph
analysis, Snowflake persistence, and MCP tools. The legacy Streamlit entrypoints
remain available until the live deployment has passed its rollout checks.

## Runtime configuration

Set `RETAILSCOPE_APP_MODE=live` and `RETAILSCOPE_STORAGE=snowflake` on Render.
Hosted live mode refuses ephemeral SQLite storage. Local development defaults to
SQLite unless Snowflake storage is explicitly selected.

Required server-side provider configuration:

- `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`,
  `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`.
- `SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT` and
  `SNOWFLAKE_PRIVATE_KEY_B64`, containing the base64-encoded PKCS8 private key.
  The launcher writes this to a restricted temporary file, never the image.
- `NIMBLE_API_KEY` for actual public-web research. Missing credentials produce
  an explicit provider error, never cached sample results.
- `RETAILSCOPE_CORTEX_MODEL=claude-sonnet-4-6`, verified in the configured account.
- `RETAILSCOPE_SESSION_SIGNING_KEY` and `RETAILSCOPE_MCP_SIGNING_KEY`: independent
  random secrets of at least 32 characters. Preserve them across deploys.
- Existing `SENTRY_DSN`, `RETAILSCOPE_RUNTIME=container`, and the desired trace
  sampling rate. Prompts, source text, credentials, and financial records are
  excluded from Sentry payloads.

Only the API process receives provider secrets. The browser bundle does not.
Apply `SNOWFLAKE_SCHEMA_SQL` from `app.workspace_store` once using the deployment
role before starting the service. Startup probes the workspace table.

For local work, `SNOWFLAKE_CONNECTION_NAME=retailscope` explicitly selects the
existing connection in `~/.snowflake/config.toml`; no connection is read implicitly.

## Access and billing

Browser workspaces use signed, HTTP-only, same-site cookies. Workspace MCP tokens
expire after seven days and are scoped to the issuing browser workspace. MCP
tokens are not valid browser cookies. A new browser profile starts a separate
workspace; account-based team sharing is not implemented by these session tokens.

LangGraph orchestrates analysis; Snowflake Cortex bills model inference to the
connected Snowflake account. Storage and SQL warehouse compute are separate
Snowflake consumption. Nimble bills discovery separately. Model responses report
actual returned token counts, not invented dollar estimates.

Requests are bounded by concurrency and owner/IP allowances, plus a process-wide
daily call cap (`RETAILSCOPE_DAILY_CALL_LIMIT`, default 120). These in-process
allowances reset on restart and are not a substitute for provider/account quotas.
Each research run uses at most three Nimble searches; analysis has bounded token
output and repair attempts. No autonomous purchase or financial transaction is
performed.

Nginx uses Render's overwritten `CF-Connecting-IP` only when `RENDER=true`.
Other deployments use the socket address. Uvicorn trusts forwarded headers only
from the local Nginx process. Do not expose the API port directly on a public host.

## Rollout gates

GitHub Actions builds and tests the frontend, runs the backend suite, and triggers
Render for the tested main-branch commit. It polls `/healthz` until the running
release matches that commit; acceptance of a deploy hook alone is not success.

Before switching live traffic, verify a real Nimble search, a sourced Cortex
answer, persistent leads and chat after restart, workspace isolation, an
authenticated MCP session, and Sentry ingestion. Required credentials must be
configured before merging: otherwise startup or the release gate will fail.

Financial scenarios use explicit assumptions and deterministic cash-flow math.
They are not invented rent rolls, audited operating statements, or certified
appraisals. The UI and model distinguish source assertions from user assumptions
and calculated scenario outcomes.
