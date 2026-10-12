import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  CircleAlert,
  Copy,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  PlugZap,
  Terminal,
  X,
} from "lucide-react";
import { api, errorMessage } from "./api";
import { installCommands, TOKEN_ENV } from "./mcp";

function CopyBlock({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="copy-block">
      <pre>
        <code>{text}</code>
      </pre>
      <button
        className="icon-button copy"
        title={`Copy ${label}`}
        aria-label={`Copy ${label}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setError("");
          } catch {
            setError("Clipboard unavailable. Select the text to copy.");
          }
        }}
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
      </button>
      {copied && (
        <span className="sr-only" role="status">
          Copied {label}
        </span>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </div>
  );
}

export function Mcp({ onBack, ready }: { onBack: () => void; ready: boolean }) {
  const [client, setClient] = useState("Codex");
  const [access, setAccess] = useState<{ token: string; expires_at: number }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now());
  const controller = useRef<AbortController | null>(null);
  const endpoint = new URL("/mcp", window.location.origin).href;
  const commands = installCommands(endpoint);
  const tokenValid = Boolean(access && access.expires_at * 1000 > now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => {
      window.clearInterval(timer);
      controller.current?.abort();
    };
  }, []);
  async function generate() {
    if (!ready || controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setError("");
    try {
      const token = await api.mcpToken(request.signal);
      if (!request.signal.aborted) {
        setAccess(token);
        setCopied(false);
        setNow(Date.now());
      }
    } catch (error) {
      if (!request.signal.aborted) setError(errorMessage(error));
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <main className="mcp-page">
      <button className="text-button" onClick={onBack}>
        <ArrowLeft size={16} />
        Workspace
      </button>
      <div className="page-heading">
        <PlugZap className="mint" size={24} />
        <div>
          <h1>Agent connections</h1>
          <p>RetailScope AI / Workspace MCP</p>
        </div>
      </div>
      <div className="mcp-grid">
        <section>
          <h2>
            <KeyRound size={18} />
            Workspace access
          </h2>
          <p className="access-description">
            Your token grants access to this workspace's saved leads and
            research. Keep it private.
          </p>
          <button
            className="primary-button token-generate"
            disabled={busy || !ready}
            onClick={generate}
          >
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <KeyRound size={15} />
            )}
            {busy
              ? "Generating token"
              : access
                ? "Generate new access token"
                : "Generate access token"}
          </button>
          {error && (
            <div className="scenario-error" role="alert">
              <CircleAlert size={15} />
              {error}
            </div>
          )}
          {access && (
            <div className="token-access">
              <label htmlFor="workspace-token">Workspace token</label>
              <div className="token-input">
                <input
                  id="workspace-token"
                  type="password"
                  readOnly
                  autoComplete="off"
                  value={access.token}
                />
                <button
                  className="icon-button"
                  title="Copy access token"
                  aria-label="Copy access token"
                  disabled={!tokenValid}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(access.token);
                      setCopied(true);
                    } catch {
                      setError(
                        "Clipboard unavailable. Generate a token in a browser with clipboard access.",
                      );
                    }
                  }}
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                </button>
                <button
                  className="icon-button"
                  title="Clear token from page"
                  aria-label="Clear token from page"
                  onClick={() => {
                    setAccess(undefined);
                    setCopied(false);
                  }}
                >
                  <X size={16} />
                </button>
              </div>
              <p className="muted">
                {tokenValid
                  ? `Expires ${new Date(access.expires_at * 1000).toLocaleString()}`
                  : "Token expired. Generate a new access token."}
              </p>
              {copied && (
                <span className="sr-only" role="status">
                  Access token copied
                </span>
              )}
            </div>
          )}
          <div className="connection-setup">
            <h2>
              <Terminal size={18} />
              Connect your agent
            </h2>
            {!tokenValid ? (
              <div className="connection-locked">
                <LockKeyhole size={20} />
                <p>Generate a workspace token to access connection commands.</p>
              </div>
            ) : (
              <>
                <h3>Token environment</h3>
                <CopyBlock
                  text={`read -r -s ${TOKEN_ENV}\nexport ${TOKEN_ENV}`}
                  label="token environment"
                />
                <div
                  role="tablist"
                  aria-label="MCP client"
                  className="client-tabs"
                >
                  {Object.keys(commands).map((label) => (
                    <button
                      key={label}
                      role="tab"
                      id={`tab-${label.replaceAll(" ", "-")}`}
                      aria-controls="client-command"
                      aria-selected={client === label}
                      tabIndex={client === label ? 0 : -1}
                      onKeyDown={(event) => {
                        if (
                          !["ArrowRight", "ArrowLeft", "Home", "End"].includes(
                            event.key,
                          )
                        )
                          return;
                        event.preventDefault();
                        const labels = Object.keys(commands);
                        const index = labels.indexOf(client);
                        const next =
                          event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? labels.length - 1
                              : (index +
                                  (event.key === "ArrowRight" ? 1 : -1) +
                                  labels.length) %
                                labels.length;
                        setClient(labels[next]);
                        document
                          .getElementById(
                            `tab-${labels[next].replaceAll(" ", "-")}`,
                          )
                          ?.focus();
                      }}
                      onClick={() => setClient(label)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div
                  id="client-command"
                  role="tabpanel"
                  aria-labelledby={`tab-${client.replaceAll(" ", "-")}`}
                  tabIndex={0}
                  key={client}
                >
                  <CopyBlock
                    text={commands[client]}
                    label={`${client} command`}
                  />
                </div>
                {client === "Generic JSON" && (
                  <p className="muted">
                    Environment expansion is client-specific.
                  </p>
                )}
              </>
            )}
          </div>
          <h3>Authenticated endpoint</h3>
          <CopyBlock text={endpoint} label="MCP endpoint" />
          <p className="muted">
            Bearer token required. Tokens last seven days.
          </p>
        </section>
        <section>
          <h2>Workspace toolkit</h2>
          <dl className="tool-list">
            {[
              [
                "list_research_leads",
                "Saved source leads in your connected workspace.",
              ],
              [
                "discover_retail_leads",
                "Live research and background Cortex analysis.",
              ],
              [
                "get_research_run",
                "Research status, leads and completed report.",
              ],
              ["ask_research_agent", "Cortex analysis of saved evidence."],
              [
                "calculate_investment_scenario",
                "Cash flows, IRR and equity multiple from explicit assumptions.",
              ],
            ].map(([name, description]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{description}</dd>
              </div>
            ))}
          </dl>
          <div className="limit-note">
            <strong>Workspace-private access</strong>
            <p>
              Anonymous access to private leads is not allowed. Tokens are held
              in memory on this page, not browser storage. Clearing the page
              does not revoke an issued token.
            </p>
            <p>
              Live research and agent analysis use configured providers and may
              incur usage charges. Scenario calculations use explicit
              assumptions, not model inference.
            </p>
          </div>
          <div className="client-docs">
            <a
              href="https://developers.openai.com/codex/mcp"
              target="_blank"
              rel="noopener noreferrer"
            >
              Codex documentation
            </a>
            <a
              href="https://code.claude.com/docs/en/mcp"
              target="_blank"
              rel="noopener noreferrer"
            >
              Claude Code documentation
            </a>
            <a
              href="https://opencode.ai/docs/mcp-servers/"
              target="_blank"
              rel="noopener noreferrer"
            >
              OpenCode documentation
            </a>
          </div>
        </section>
      </div>
    </main>
  );
}
