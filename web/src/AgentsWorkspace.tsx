import { useEffect, useRef, useState, type FormEvent } from "react";
import { Inbox, Pause, Play, Plus, RefreshCw } from "lucide-react";
import { api, errorMessage, type Agent, type Lead } from "./api";
import { streamResearch, stageLabel } from "./researchStream";
import { SearchBuilder, defaultCriteria, searchPrompt } from "./SearchBuilder";
import { LeadDetails } from "./LeadDetails";

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "Not yet";
}

function expired(agent: Agent) {
  return !(Date.parse(agent.expires_at) > Date.now());
}

export function AgentsWorkspace({
  ready,
  active,
  onOpenReport,
  onResearchLead,
}: {
  ready: boolean;
  active: boolean;
  onOpenReport: (id: string) => void;
  onResearchLead: (lead: Lead) => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<Lead>();
  const [criteria, setCriteria] = useState(defaultCriteria);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(active);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState("");
  const [runs, setRuns] = useState<
    Record<string, { id: string; status: string }>
  >({});
  const action = useRef<AbortController | null>(null);
  const reads = useRef(new Map<string, AbortController>());
  const readIds = useRef(new Set<string>());
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    if (active) setStarted(true);
  }, [active]);
  useEffect(
    () => () => {
      action.current?.abort();
      reads.current.forEach((controller) => controller.abort());
    },
    [],
  );
  useEffect(() => {
    if (!ready || !started) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const tasksRequest = api.agents(controller.signal).then((tasks) => {
      if (controller.signal.aborted) return;
      setAgents(tasks.agents);
      setRuns((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([id, run]) => {
            const agent = tasks.agents.find((item) => item.id === id);
            return (
              !agent?.last_job_id ||
              agent.last_job_id === run.id ||
              action.current !== null
            );
          }),
        ),
      );
    });
    const inboxRequest = api.leads(controller.signal).then((inbox) => {
      if (controller.signal.aborted) return;
      setLeads(
        inbox.leads.map((lead) =>
          readIds.current.has(lead.id)
            ? { ...lead, read_at: lead.read_at || "read" }
            : lead,
        ),
      );
    });
    let timer: number;
    function scheduleRefresh() {
      timer = window.setTimeout(() => {
        if (activeRef.current) setVersion((value) => value + 1);
        else scheduleRefresh();
      }, 30000);
    }
    Promise.allSettled([tasksRequest, inboxRequest])
      .then((results) => {
        if (controller.signal.aborted) return;
        const failure = results.find((result) => result.status === "rejected");
        if (failure?.status === "rejected")
          setError(errorMessage(failure.reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
          scheduleRefresh();
        }
      });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [ready, started, version]);

  async function perform(
    key: string,
    operation: (signal: AbortSignal) => Promise<void>,
  ) {
    if (action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(key);
    setError("");
    try {
      await operation(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    } finally {
      if (action.current === controller) {
        action.current = null;
        if (!controller.signal.aborted) {
          setBusy("");
          setVersion((value) => value + 1);
        }
      }
    }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    if (
      !name.trim() ||
      !Number.isFinite(criteria.min_price) ||
      !Number.isFinite(criteria.max_price) ||
      criteria.min_price < 0 ||
      criteria.max_price <= criteria.min_price
    )
      return;
    void perform("create", async (signal) => {
      const agent = await api.createAgent(
        { ...criteria, name: name.trim() },
        signal,
      );
      if (signal.aborted) return;
      setAgents((current) => [...current, agent]);
      setCreating(false);
      setName("");
    });
  }
  function run(agent: Agent) {
    void perform(agent.id, async (signal) => {
      const job = await api.runAgent(agent.id, signal);
      if (signal.aborted) return;
      setRuns((current) => ({ ...current, [agent.id]: job }));
      try {
        const result = await streamResearch(job.id, signal, (snapshot) => {
          if (!signal.aborted)
            setRuns((current) => ({
              ...current,
              [agent.id]: {
                id: job.id,
                status: snapshot.stage || snapshot.status,
              },
            }));
          if (!signal.aborted && snapshot.leads.length)
            setLeads((current) => [
              ...snapshot.leads,
              ...current.filter(
                (lead) => !snapshot.leads.some((next) => next.id === lead.id),
              ),
            ]);
        });
        if (!signal.aborted && result.error) setError(result.error);
      } catch (reason) {
        if (!signal.aborted)
          setRuns((current) => ({
            ...current,
            [agent.id]: { id: job.id, status: "Check report" },
          }));
        throw reason;
      }
    });
  }
  async function select(lead: Lead) {
    setSelected(lead);
    if (lead.read_at || reads.current.has(lead.id)) return;
    const controller = new AbortController();
    reads.current.set(lead.id, controller);
    try {
      await api.readLead(lead.id, controller.signal);
      if (!controller.signal.aborted) {
        readIds.current.add(lead.id);
        setLeads((current) =>
          current.map((item) =>
            item.id === lead.id ? { ...item, read_at: "read" } : item,
          ),
        );
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    } finally {
      reads.current.delete(lead.id);
    }
  }
  const unread = leads.filter((lead) => !lead.read_at).length;
  const enrolled = agents.filter((agent) => !expired(agent)).length;
  const visible = leads
    .filter(
      (lead) =>
        (filter !== "unread" || !lead.read_at) &&
        (!agentFilter || lead.agent_id === agentFilter),
    )
    .sort(
      (left, right) =>
        (Date.parse(right.retrieved_at || "") || 0) -
        (Date.parse(left.retrieved_at || "") || 0),
    );
  if (!active) return null;
  return (
    <section
      className="agents-workspace"
      aria-label="Background agents and inbox"
    >
      <div className="agents-heading">
        <div>
          <h1>Background agents</h1>
          <p className="muted">
            {agents.filter((agent) => agent.enabled && !expired(agent)).length}{" "}
            active / {agents.length} tasks · Every 6 hours
          </p>
        </div>
        <div className="agent-actions">
          <button
            className="icon-button"
            title="Refresh agents and inbox"
            aria-label="Refresh agents and inbox"
            disabled={loading}
            onClick={() => {
              setError("");
              setVersion((value) => value + 1);
            }}
          >
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
          <button
            className="primary-button"
            disabled={!ready || (!creating && enrolled >= 5)}
            title={enrolled >= 5 ? "Five agents per workspace" : undefined}
            onClick={() => setCreating(!creating)}
          >
            <Plus size={16} />
            {creating ? "Close form" : "New agent"}
          </button>
        </div>
      </div>
      {error && (
        <div className="notice" role="alert">
          {error}
        </div>
      )}
      {creating && (
        <form className="agent-create" onSubmit={create}>
          <label>
            Agent name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={80}
            />
          </label>
          <SearchBuilder
            value={criteria}
            onChange={setCriteria}
            disabled={Boolean(busy)}
          />
          <details className="edit-prompt">
            <summary>Prompt preview</summary>
            <p className="prompt-preview">{searchPrompt(criteria)}</p>
          </details>
          <p className="muted">
            First run: next 00:17, 06:17, 12:17 or 18:17 UTC.
          </p>
          <button
            className="primary-button"
            disabled={Boolean(busy) || enrolled >= 5}
            type="submit"
          >
            <Plus size={15} />
            Save agent
          </button>
        </form>
      )}
      {loading && !agents.length && (
        <p role="status" className="muted">
          Loading tasks
        </p>
      )}
      {!loading && !agents.length && (
        <p className="muted">No background agents yet.</p>
      )}
      <div className="agent-list">
        {agents.map((agent) => (
          <article className="agent-row" key={agent.id}>
            <div>
              <h2>{agent.name}</h2>
              <p>
                {agent.city} · ${(agent.min_price / 1000000).toLocaleString()}M
                - ${(agent.max_price / 1000000).toLocaleString()}M ·{" "}
                {agent.strategy}
              </p>
              <p className="muted">
                {expired(agent)
                  ? "Expired"
                  : agent.enabled
                    ? "Active"
                    : "Paused"}{" "}
                · Every {agent.cadence_hours}h ·{" "}
                {leads.filter((lead) => lead.agent_id === agent.id).length}{" "}
                results
              </p>
            </div>
            <div className="run-metadata">
              <span>Last run: {timestamp(agent.last_run_at)}</span>
              <span>
                Next:{" "}
                {expired(agent)
                  ? "Expired"
                  : agent.enabled
                    ? timestamp(agent.next_run_at)
                    : "Paused"}
              </span>
              <span>Expires: {timestamp(agent.expires_at)}</span>
              <span role="status">
                {stageLabel(
                  runs[agent.id]?.status || agent.last_status || "Not run yet",
                )}
              </span>
              {agent.last_error && (
                <span className="error-text">{agent.last_error}</span>
              )}
            </div>
            <div className="agent-actions">
              <button
                className="icon-button"
                title={
                  expired(agent)
                    ? "Expired; create a new agent"
                    : agent.enabled
                      ? "Pause agent"
                      : "Resume agent"
                }
                aria-label={`${agent.enabled ? "Pause" : "Resume"} ${agent.name}`}
                disabled={Boolean(busy) || expired(agent)}
                onClick={() =>
                  void perform(agent.id, async (signal) => {
                    const updated = await api.updateAgent(
                      agent.id,
                      !agent.enabled,
                      signal,
                    );
                    if (!signal.aborted)
                      setAgents((current) =>
                        current.map((item) =>
                          item.id === updated.id ? updated : item,
                        ),
                      );
                  })
                }
              >
                {agent.enabled ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <button
                className="text-button"
                disabled={Boolean(busy) || expired(agent)}
                onClick={() => run(agent)}
              >
                <Play size={14} />
                Run now
              </button>
              {(runs[agent.id]?.id || agent.last_job_id) && (
                <button
                  className="text-button"
                  onClick={() =>
                    onOpenReport(runs[agent.id]?.id || agent.last_job_id!)
                  }
                >
                  Open report
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      <div className="inbox-heading">
        <h2>
          <Inbox size={18} />
          Inbox{" "}
          <span className="count">
            {unread} unread / {leads.length}
          </span>
        </h2>
        <div className="inbox-filters">
          <label className="sr-only" htmlFor="inbox-read">
            Read status
          </label>
          <select
            id="inbox-read"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All results</option>
            <option value="unread">Unread</option>
          </select>
          <label className="sr-only" htmlFor="inbox-agent">
            Filter by agent
          </label>
          <select
            id="inbox-agent"
            value={agentFilter}
            onChange={(event) => setAgentFilter(event.target.value)}
          >
            <option value="">All agents</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="inbox-layout">
        <div className="inbox-list" aria-label="Inbox results">
          {visible.map((lead) => (
            <button
              className={`lead-row ${selected?.id === lead.id ? "selected" : ""}`}
              key={lead.id}
              aria-pressed={selected?.id === lead.id}
              onClick={() => void select(lead)}
            >
              <h3>{lead.title}</h3>
              <p className="muted">
                {lead.location && lead.location !== "unknown"
                  ? lead.location
                  : "Location not provided"}{" "}
                · {lead.read_at ? "Read" : "Unread"}
              </p>
            </button>
          ))}
          {!loading && !visible.length && (
            <p className="muted">No matching results.</p>
          )}
        </div>
        <aside
          className="inbox-details property-content"
          aria-label="Inbox property"
        >
          {selected ? (
            <>
              <LeadDetails lead={selected} />
              <div className="agent-actions">
                <button
                  className="text-button"
                  onClick={() => onResearchLead(selected)}
                >
                  Research this property
                </button>
                {selected.job_id && (
                  <button
                    className="text-button"
                    onClick={() => onOpenReport(selected.job_id!)}
                  >
                    Open report
                  </button>
                )}
              </div>
            </>
          ) : (
            <p className="muted">No property selected</p>
          )}
        </aside>
      </div>
    </section>
  );
}
