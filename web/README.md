# RetailScope Web

React + TypeScript + Vite frontend. No leads or chat answers are seeded; the UI uses API responses only.

## Local development

```sh
cd web
npm ci
npm run dev
```

Open `/` for the workspace and `/landing` for MCP connections (`/landing/mcp` remains an alias). The Vite development proxy forwards `/api` and `/mcp` to `http://127.0.0.1:8765`, preserving Host for the backend's same-origin check; it does not provide or modify a backend. Missing API routes show loading/error/empty states, never substitute data. Production hosting must serve the built `dist/` with SPA history fallback and same-origin API/MCP routing.

```sh
npm test
npm run build
```

## API contract

- `GET /api/status`: `{providers: {snowflake: boolean, nimble: boolean}, model: string}`
- `GET /api/leads`: `{leads: [{id, title, url, location?, summary?, status?, retrieved_at?}]}`
- `POST /api/research`: `{query}` -> `{id, status}`
- `GET /api/research/{id}`: `{id, status, leads?: [], error?, analysis?: {answer, sources, usage?, model?}}`
- `POST /api/chat`: `{message, lead_id?, conversation_id?, scenario?}` -> `{answer, sources: [{title, url}], conversation_id, usage?}`
- `POST /api/scenarios`: explicit user assumptions -> `{irr, equity_multiple, initial_equity, annual_cash_flows, assumptions}`
- `GET /api/conversations`: `{conversations: [{id, updated_at, preview}]}`
- `GET /api/conversations/{id}`: `{id, messages: [{role, content}]}`
- `POST /api/mcp-token`: `{}` -> `{token, expires_at}` (Unix seconds)

The first request awaits `/api/status` before leads, history, or a restored thread are fetched. The backend sets an opaque HTTP-only session cookie; the frontend does not read or parse it.

Research polls every two seconds, for at most 45 responses, with a two-minute total deadline. Pending states: `pending`, `queued`, `running`, `processing`, `in_progress`. Completion states: `completed`, `complete`, `done`, `succeeded`, `success`. Failures: `failed`, `error`, `cancelled`, `canceled`. Unexpected states fail visibly. Individual API requests time out after 30 seconds. Research and chat can be cancelled.

The visible conversation is scoped to a selected lead or ad hoc context. Persisted conversations are available from the history picker. The selected thread ID is kept in the URL's `conversation` query parameter and fetched again on reload; no browser storage is used. Switching property does not attach an in-flight answer to another property. Chat Markdown excludes raw HTML and remote images; source links accept only absolute HTTP/HTTPS URLs.

Completed research analysis is displayed in the ad hoc chat thread with its sources and model when supplied. Pending jobs may omit leads. Provider booleans indicate configured credentials, not network health. If analysis fails, refresh leads to retrieve results already persisted by the API.

Selected-property underwriting starts with blank purchase price, annual NOI and exit cap rate; hold period defaults to seven years. Optional loan-to-value, interest and NOI-growth inputs remain blank. Percentage inputs are converted to decimal API rates. The server supplies all calculated metrics and cash flows. Editing assumptions invalidates previous results and attachments. A calculated scenario can be attached to property chat as its raw user assumptions; API defaults are shown in the result's assumptions.

MCP uses the current origin's `/mcp` endpoint and requires a workspace bearer token. Connection commands are gated until a token is generated. Tokens are masked, held only in component memory, never included in generated commands, and valid for seven days. Token generation errors (including missing signing configuration) are surfaced without inventing access credentials. Clearing a token from the page does not revoke it.

Codex uses `--bearer-token-env-var RETAILSCOPE_MCP_TOKEN`; Claude Code uses an Authorization header; OpenCode uses its `{env:RETAILSCOPE_MCP_TOKEN}` syntax with OAuth disabled. Generic JSON's environment expansion depends on the client. Commands include no anonymous access or public-demo claims. Live discovery and analysis may incur provider usage; deterministic scenario calculations do not invoke a model.

Official client references: [Codex](https://developers.openai.com/codex/mcp), [Claude Code](https://code.claude.com/docs/en/mcp), [OpenCode](https://opencode.ai/docs/mcp-servers/).
