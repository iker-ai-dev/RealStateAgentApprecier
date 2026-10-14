import { useEffect, useRef, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { api, errorMessage, type ProviderChecks, type ProviderStatus } from "./api";

export function ProviderConnections({ status }: { status?: ProviderStatus }) {
  const [checks, setChecks] = useState<ProviderChecks>();
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => pending.current?.abort(), []);

  async function check() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setChecking(true);
    setError("");
    setChecks(undefined);
    try {
      const result = await api.checkProviders(controller.signal);
      if (!controller.signal.aborted) setChecks(result);
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted) setChecking(false);
      pending.current = undefined;
    }
  }

  return (
    <div className="provider-connections" aria-label="Provider connections">
      <div className="provider-pills" aria-live="polite">
        {(["snowflake", "nimble"] as const).map((name) => {
          const nameLabel = name === "snowflake" ? "Snowflake" : "Nimble";
          const result = checks?.providers[name];
          const state = checking ? "checking" : result?.state ?? (status ? (status.providers[name] ? "configured" : "not_configured") : "unknown");
          const label = { checking: "Checking", connected: "Connected", configured: "Configured", not_configured: "Not configured", error: "Unavailable", unknown: "Unknown" }[state];
          const detail = result
            ? `${result.message} Checked ${new Date(result.checked_at).toLocaleTimeString()}.`
            : state === "configured" ? "Credentials configured; connection not yet checked." : label;
          return (
            <span key={name} className={`provider ${state}`} title={`${nameLabel}: ${label}. ${detail}`} aria-label={`${nameLabel}: ${label}. ${detail}`}>
              <span className="status-dot" aria-hidden="true" />
              <span>{nameLabel}<small>{label}</small></span>
            </span>
          );
        })}
      </div>
      <button className="icon-button" type="button" disabled={!status || checking} onClick={check}
        aria-label="Check provider connections" title="Check Snowflake and Nimble connections (no search or AI inference)">
        {checking ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
      </button>
      {error && <span className="provider-check-error" role="alert">{error}</span>}
    </div>
  );
}
