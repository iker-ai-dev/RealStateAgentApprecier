import { useEffect, useRef, useState, type FormEvent } from "react";
import { MessageSquarePlus, Pause, Play, Plus, RefreshCw } from "lucide-react";
import { api, errorMessage, type Agent, type AgentRun, type Lead } from "./api";
import { streamResearch, stageLabel } from "./researchStream";
import { SearchBuilder, defaultCriteria, searchPrompt } from "./SearchBuilder";

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "Not yet";
}
function expired(agent: Agent) {
  return !(Date.parse(agent.expires_at) > Date.now());
}

export function AgentsWorkspace({
  ready, active, selectedId, onOpenReport, onAgentsChanged,
  onSelectAgent, onStartChat, chatBusy = false, refreshKey = 0,
}: {
  ready: boolean;
  active: boolean;
  selectedId?: string;
  onOpenReport: (id: string) => void;
  onResearchLead?: (lead: Lead) => void;
  onAgentsChanged?: () => void;
  onSelectAgent?: (id: string) => void;
  onStartChat?: (prompt: string) => void;
  chatBusy?: boolean;
  refreshKey?: number;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [history, setHistory] = useState<{ agentId: string; runs: AgentRun[] }>();
  const [expandedRun, setExpandedRun] = useState<string>();
  const [criteria, setCriteria] = useState(defaultCriteria);
  const [name, setName] = useState("");
  const [cadence, setCadence] = useState<6 | 12 | 24>(6);
  const [error, setError] = useState<{ key?: string; message: string }>();
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState("");
  const [runs, setRuns] = useState<Record<string, { id: string; status: string }>>({});
  const action = useRef<AbortController | null>(null);
  const callbacks = useRef({ onAgentsChanged, onSelectAgent });
  callbacks.current = { onAgentsChanged, onSelectAgent };

  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => setExpandedRun(undefined), [selectedId]);
  useEffect(() => {
    if (!ready || !active) return;
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    async function load() {
      try {
        const tasks = await api.agents(controller.signal);
        if (controller.signal.aborted) return;
        setAgents(tasks.agents);
        setLoaded(true);
        if (selectedId) {
          const result = await api.agentRuns(selectedId, controller.signal);
          if (!controller.signal.aborted) setHistory({
            agentId: selectedId,
            runs: [...result.runs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)),
          });
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError({ key: selectedId, message: errorMessage(reason) });
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(() => setVersion((value) => value + 1), 30000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [ready, active, selectedId, version, refreshKey]);

  async function perform(key: string, operation: (signal: AbortSignal) => Promise<void>) {
    if (!ready || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(key);
    setError(undefined);
    try {
      await operation(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted)
        setError({ key: key === "create" ? undefined : key, message: errorMessage(reason) });
    } finally {
      if (action.current === controller) {
        action.current = null;
        if (!controller.signal.aborted) {
          setBusy("");
          setVersion((value) => value + 1);
          callbacks.current.onAgentsChanged?.();
        }
      }
    }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    if (!loaded || !name.trim() || enrolled >= 5 ||
      !Number.isFinite(criteria.min_price) || !Number.isFinite(criteria.max_price) ||
      criteria.min_price < 0 || criteria.max_price <= criteria.min_price) return;
    void perform("create", async (signal) => {
      const agent = await api.createAgent(
        { ...criteria, name: name.trim(), cadence_hours: cadence }, signal,
      );
      if (signal.aborted) return;
      setAgents((current) => [...current, agent]);
      setName("");
      callbacks.current.onSelectAgent?.(agent.id);
    });
  }
  function run(agent: Agent) {
    void perform(agent.id, async (signal) => {
      const job = await api.runAgent(agent.id, signal);
      if (signal.aborted) return;
      setRuns((current) => ({ ...current, [agent.id]: job }));
      setVersion((value) => value + 1);
      callbacks.current.onAgentsChanged?.();
      try {
        const result = await streamResearch(job.id, signal, (snapshot) => {
          if (!signal.aborted) setRuns((current) => ({
            ...current, [agent.id]: { id: job.id, status: snapshot.stage || snapshot.status },
          }));
        });
        if (!signal.aborted) {
          setRuns((current) => ({ ...current, [agent.id]: { id: job.id, status: result.status } }));
          if (result.error) setError({ key: agent.id, message: result.error });
        }
      } catch (reason) {
        if (!signal.aborted) setRuns((current) => ({
          ...current, [agent.id]: { id: job.id, status: "Check report" },
        }));
        throw reason;
      }
    });
  }
  const enrolled = agents.filter((agent) => !expired(agent)).length;
  const agent = agents.find((item) => item.id === selectedId);
  const visibleRuns = history && history.agentId === selectedId ? history.runs : undefined;
  const currentRun = agent && runs[agent.id];
  const running = busy === selectedId || visibleRuns?.some((item) => ["queued", "running"].includes(item.status));
  if (!active) return null;
  return (
    <section className="agents-workspace" aria-label="Background agents">
      <div className="agents-heading">
        <h1>{selectedId ? agent?.name || "Agent" : "New agent"}</h1>
        <button className="icon-button" title="Refresh agent" aria-label="Refresh agent"
          disabled={!ready || loading} onClick={() => setVersion((value) => value + 1)}>
          <RefreshCw size={16} className={loading ? "spin" : ""} />
        </button>
      </div>
      {error && error.key === selectedId && <div className="notice" role="alert">{error.message}</div>}
      {!selectedId ? (
        <form className="agent-create" onSubmit={create}>
          <label>Agent name<input value={name} onChange={(event) => setName(event.target.value)}
            required maxLength={80} disabled={!ready || Boolean(busy)} /></label>
          <SearchBuilder value={criteria} onChange={setCriteria} disabled={!ready || Boolean(busy)} />
          <details className="edit-prompt"><summary>Prompt preview</summary>
            <p className="prompt-preview">{searchPrompt(criteria)}</p></details>
          <label>Run cadence<select value={cadence} disabled={!ready || Boolean(busy)}
            onChange={(event) => setCadence(Number(event.target.value) as 6 | 12 | 24)}>
            <option value={6}>Every 6 hours</option><option value={12}>Every 12 hours</option>
            <option value={24}>Every 24 hours</option>
          </select></label>
          {enrolled >= 5 && <p className="muted">Five agents per workspace</p>}
          <button className="primary-button" disabled={!ready || !loaded || Boolean(busy) || enrolled >= 5} type="submit">
            <Plus size={15} />Confirm and save agent
          </button>
        </form>
      ) : agent ? (
        <>
          <div className="agent-row">
            <div>
              <p>{agent.city} · ${(agent.min_price / 1000000).toLocaleString()}M
                - ${(agent.max_price / 1000000).toLocaleString()}M · {agent.strategy}</p>
              <p className="muted">{expired(agent) ? "Expired" : agent.enabled ? "Active" : "Paused"}
                {" · "}Every {agent.cadence_hours} hours</p>
              <details className="edit-prompt">
                <summary>Research prompt</summary>
                <p className="prompt-preview">{agent.query}</p>
              </details>
            </div>
            <div className="run-metadata">
              <span>Last run: {timestamp(agent.last_run_at)}</span>
              <span>Next: {expired(agent) ? "Expired" : agent.enabled ? timestamp(agent.next_run_at) : "Paused"}</span>
              <span>Expires: {timestamp(agent.expires_at)}</span>
              <span role="status">{stageLabel(
                currentRun && (busy === agent.id || currentRun.id === agent.last_job_id)
                  ? currentRun.status : agent.last_status || "Not run yet",
              )}</span>
              {agent.last_error && !running && <span className="error-text">{agent.last_error}</span>}
            </div>
            <div className="agent-actions">
              <button className="icon-button" title={expired(agent) ? "Expired; create a new agent" : agent.enabled ? "Pause agent" : "Resume agent"}
                aria-label={`${agent.enabled ? "Pause" : "Resume"} ${agent.name}`}
                disabled={!ready || Boolean(busy) || expired(agent)}
                onClick={() => void perform(agent.id, async (signal) => {
                  const updated = await api.updateAgent(agent.id, !agent.enabled, signal);
                  if (!signal.aborted) setAgents((current) => current.map((item) => item.id === updated.id ? updated : item));
                })}>
                {agent.enabled ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <button className="text-button" disabled={!ready || Boolean(busy) || Boolean(running) || !visibleRuns || expired(agent)}
                onClick={() => run(agent)}><Play size={14} />Run now</button>
              {onStartChat && <button className="text-button" disabled={!ready || chatBusy}
                onClick={() => onStartChat(agent.query)}><MessageSquarePlus size={16} />New chat from agent</button>}
            </div>
          </div>
          <section className="agent-run-history" aria-label="Run history">
            <h2>Run history</h2>
            {!visibleRuns && loading && <p role="status">Loading run history</p>}
            {visibleRuns?.length === 0 && <p className="muted">No runs yet.</p>}
            {visibleRuns?.map((item) => {
              const opensReport = ["completed", "partial"].includes(item.status);
              const expanded = !opensReport && expandedRun === item.id;
              const detailId = `agent-run-detail-${item.id}`;
              return <div key={item.id}>
              <button className="agent-run-row"
                aria-expanded={opensReport ? undefined : expanded}
                aria-controls={opensReport ? undefined : detailId}
                onClick={() => opensReport ? onOpenReport(item.id) : setExpandedRun(expanded ? undefined : item.id)}>
                <span>{timestamp(item.created_at)}</span>
                <span>{stageLabel(item.status)}</span>
                <span>{item.lead_count} {item.lead_count === 1 ? "lead" : "leads"}</span>
                <span>{opensReport ? "Open report" : expanded ? "Hide details" : "View run"}</span>
                {item.error && <span className="error-text">{item.error}</span>}
              </button>
              {expanded && <div className="agent-run-detail" id={detailId} role="region" aria-label="Run details">
                <dl>
                  <dt>Query</dt><dd>{item.query}</dd>
                  <dt>Status</dt><dd>{stageLabel(item.status)}</dd>
                  <dt>Run ID</dt><dd>{item.id}</dd>
                  {item.error && <><dt>Error</dt><dd className="error-text">{item.error}</dd></>}
                </dl>
              </div>}
              </div>;
            })}
          </section>
        </>
      ) : <p className="muted" role="status">{!ready || loading ? "Loading agent" : "Agent not found."}</p>}
    </section>
  );
}
