import { useEffect, useState } from "react";
import { Download, MessageSquare, RefreshCw } from "lucide-react";
import {
  api,
  errorMessage,
  displayTitle,
  preferredProperty,
  safeUrl,
  type Artifact,
  type ArtifactSummary,
  type CalculatedScenario,
} from "./api";
import { LeadDetails } from "./LeadDetails";
import { Markdown } from "./Markdown";
import { ScenarioForm } from "./ScenarioForm";

export type ArtifactsWorkspaceProps = {
  ready: boolean;
  active: boolean;
  refreshKey?: number;
  selectedId?: string;
  onSelect?: (id: string) => void;
  onOpenChat: (id: string, scenario?: CalculatedScenario) => void;
};

function download(artifact: Artifact) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(artifact, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `${artifact.title.replace(/[^a-z0-9_-]+/gi, "-").slice(0, 80) || "artifact"}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ArtifactsWorkspace({
  ready,
  active,
  refreshKey = 0,
  selectedId,
  onSelect,
  onOpenChat,
}: ArtifactsWorkspaceProps) {
  const [summaries, setSummaries] = useState<ArtifactSummary[]>([]);
  const [cache, setCache] = useState<Record<string, Artifact>>({});
  const [localId, setLocalId] = useState<string>();
  const [sourceIds, setSourceIds] = useState<Record<string, string>>({});
  const [propertyIds, setPropertyIds] = useState<Record<string, string>>({});
  const [scenarios, setScenarios] = useState<
    Record<string, CalculatedScenario | undefined>
  >({});
  const [filter, setFilter] = useState("");
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const id = selectedId ?? localId;
  const artifact = id ? cache[id] : undefined;

  useEffect(() => {
    if (!ready || !active) return;
    const controller = new AbortController();
    setLoading(true);
    setListError("");
    api
      .artifacts(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setSummaries(result.artifacts);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setListError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [ready, active, refreshKey, version]);

  useEffect(() => {
    if (!ready || !active || !id) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    api
      .artifact(id, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.id !== id)
          throw new Error(
            "The returned artifact does not match the requested document.",
          );
        setCache((current) => ({ ...current, [id]: result }));
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setDetailError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [ready, active, id, refreshKey, version]);

  const visible = summaries.filter((item) =>
    `${item.title} ${item.query}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const selectedLead =
    artifact?.leads.find((lead) => lead.id === sourceIds[artifact.id]) ??
    artifact?.leads[0];
  const refresh = () => setVersion((value) => value + 1);
  const sourceKey = JSON.stringify([artifact?.id, selectedLead?.id]);
  const selectedProperty = selectedLead?.properties?.find((property) => property.id === propertyIds[sourceKey]) ?? preferredProperty(selectedLead);
  const scenarioKey = selectedProperty ? JSON.stringify([artifact?.id, selectedLead?.id, selectedProperty.id]) : sourceKey;
  return (
    <section
      className="artifacts-workspace"
      aria-label="Artifacts"
      hidden={!active}
    >
      <div className="agents-heading">
        <div>
          <h1>Artifacts</h1>
          <p className="muted">{summaries.length} saved reports</p>
        </div>
        <button
          className="icon-button"
          title="Refresh artifacts"
          aria-label="Refresh artifacts"
          disabled={!ready || loading || detailLoading}
          onClick={refresh}
        >
          <RefreshCw
            size={16}
            className={loading || detailLoading ? "spin" : ""}
          />
        </button>
      </div>
      {listError && (
        <div className="notice" role="alert">
          {listError}
          <button className="text-button" onClick={refresh}>
            Retry artifacts
          </button>
        </div>
      )}
      <div className="artifacts-layout">
        <aside className="artifacts-list" aria-label="Saved artifacts">
          <label>
            Search artifacts
            <input
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
          {loading && !summaries.length && (
            <p role="status">Loading artifacts</p>
          )}
          {!loading && !listError && !visible.length && (
            <p className="muted">
              {filter ? "No matching artifacts." : "No saved artifacts yet."}
            </p>
          )}
          {visible.map((item) => (
            <button
              key={item.id}
              className={`lead-row artifact-row ${id === item.id ? "selected" : ""}`}
              aria-pressed={id === item.id}
              onClick={() => {
                setLocalId(item.id);
                onSelect?.(item.id);
              }}
            >
              <h2>{displayTitle(item.title)}</h2>
              <p className="muted">
                {item.status} · {new Date(item.created_at).toLocaleString()}
              </p>
              {item.lead_count != null && (
                <span className="muted">{item.lead_count} sources</span>
              )}
            </button>
          ))}
        </aside>
        <div
          className="artifact-detail"
          aria-label="Artifact document"
          aria-busy={detailLoading}
        >
          {detailError && (
            <div className="notice" role="alert">
              {detailError}
              <button className="text-button" onClick={refresh}>
                Retry document
              </button>
            </div>
          )}
          {detailLoading && !artifact && <p role="status">Loading document</p>}
          {!id && <p className="muted">No artifact selected.</p>}
          {artifact && (
            <>
              <div className="agents-heading">
                <div>
                  <h2>{displayTitle(artifact.title)}</h2>
                  <p className="muted">
                    {artifact.status} ·{" "}
                    {new Date(artifact.created_at).toLocaleString()}
                  </p>
                </div>
                <div className="agent-actions">
                  <button
                    className="icon-button"
                    title="Download document JSON"
                    aria-label="Download document JSON"
                    onClick={() => download(artifact)}
                  >
                    <Download size={16} />
                  </button>
                  <button
                    className="text-button"
                    onClick={() => onOpenChat(artifact.id)}
                  >
                    <MessageSquare size={16} />
                    Open chat
                  </button>
                </div>
              </div>
              <p className="artifact-query">{artifact.query}</p>
              {artifact.error && (
                <div className="notice" role="alert">
                  {artifact.error}
                </div>
              )}
              {artifact.status === "partial" && (
                <p role="status" className="muted">
                  Partial report. Some research could not be completed.
                </p>
              )}
              <article className="artifact-narrative markdown">
                {artifact.report?.answer ? (
                  <Markdown>{artifact.report.answer}</Markdown>
                ) : (
                  <p className="muted">
                    {["queued", "pending", "running"].includes(artifact.status)
                      ? "Report in progress."
                      : "Analysis unavailable."}
                  </p>
                )}
                {!!artifact.report?.sources.length && (
                  <section
                    className="artifact-citations"
                    aria-label="Report citations"
                  >
                    <h3>Citations</h3>
                    <ul>
                      {artifact.report.sources.map((source, index) => {
                        const url = safeUrl(source.url);
                        return (
                          <li key={`${source.url}-${index}`}>
                            {url ? (
                              <a
                                href={url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                {source.title}
                              </a>
                            ) : (
                              <span>{source.title} (link unavailable)</span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                )}
              </article>
              <h3>Sources ({artifact.leads.length})</h3>
              {!artifact.leads.length ? (
                <p className="muted">No saved sources.</p>
              ) : (
                <div className="inbox-layout artifact-sources">
                  <div className="inbox-list" aria-label="Artifact sources">
                    {artifact.leads.map((lead) => (
                      <button
                        className={`lead-row ${selectedLead?.id === lead.id ? "selected" : ""}`}
                        key={lead.id}
                        aria-pressed={selectedLead?.id === lead.id}
                        onClick={() =>
                          setSourceIds((current) => ({
                            ...current,
                            [artifact.id]: lead.id,
                          }))
                        }
                      >
                        {lead.title}
                        {Boolean(lead.properties?.length) && <small className="source-property-count">{lead.properties!.length} properties</small>}
                      </button>
                    ))}
                  </div>
                  <aside
                    className="inbox-details property-content"
                    aria-label="Source details"
                  >
                    {selectedLead && (
                      <>
                        <LeadDetails lead={selectedLead} selectedPropertyId={selectedProperty?.id} onPropertySelect={(id) => setPropertyIds((current) => ({ ...current, [sourceKey]: id }))} />
                        <details
                          className="artifact-scenario"
                          key={scenarioKey}
                        >
                          <summary>Financial scenario</summary>
                          <ScenarioForm
                            calculated={scenarios[scenarioKey]}
                            onCalculated={(scenario) =>
                              setScenarios((current) => ({
                                ...current,
                                [scenarioKey]: scenario,
                              }))
                            }
                            onInvalidate={() =>
                              setScenarios((current) => ({
                                ...current,
                                [scenarioKey]: undefined,
                              }))
                            }
                            onAsk={() => onOpenChat(artifact.id, scenarios[scenarioKey])}
                          />
                        </details>
                      </>
                    )}
                  </aside>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
