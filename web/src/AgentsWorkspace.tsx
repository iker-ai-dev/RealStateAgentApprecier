import { useEffect, useRef, useState, type FormEvent } from "react";
import { Pause, Play, Plus, RefreshCw } from "lucide-react";
import { api, errorMessage, type Agent, type Lead } from "./api";
import { streamResearch, stageLabel } from "./researchStream";
import { SearchBuilder, defaultCriteria, searchPrompt } from "./SearchBuilder";

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
  refreshKey = 0,
}: {
  ready: boolean;
  active: boolean;
  onOpenReport: (id: string) => void;
  onResearchLead: (lead: Lead) => void;
  refreshKey?: number;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [criteria, setCriteria] = useState(defaultCriteria);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [cadence, setCadence] = useState<6 | 12 | 24>(6);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(active);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState("");
  const [runs, setRuns] = useState<
    Record<string, { id: string; status: string }>
  >({});
  const action = useRef<AbortController | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    if (active) setStarted(true);
  }, [active]);
  useEffect(
    () => () => {
      action.current?.abort();
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
    let timer: number;
    function scheduleRefresh() {
      timer = window.setTimeout(() => {
        if (activeRef.current) setVersion((value) => value + 1);
        else scheduleRefresh();
      }, 30000);
    }
    Promise.allSettled([tasksRequest])
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
  }, [ready, started, version, refreshKey]);

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
        { ...criteria, name: name.trim(), cadence_hours: cadence },
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
  const enrolled = agents.filter((agent) => !expired(agent)).length;
  if (!active) return null;
  return (
    <section className="agents-workspace" aria-label="Background agents">
      <div className="agents-heading">
        <div>
          <h1>Background agents</h1>
          <p className="muted">
            {agents.filter((agent) => agent.enabled && !expired(agent)).length}{" "}
            active / {agents.length} tasks
          </p>
        </div>
        <div className="agent-actions">
          <button
            className="icon-button"
            title="Refresh agents"
            aria-label="Refresh agents"
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
          <label>
            Run cadence
            <select
              value={cadence}
              disabled={Boolean(busy)}
              onChange={(event) =>
                setCadence(Number(event.target.value) as 6 | 12 | 24)
              }
            >
              <option value={6}>Every 6 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Every 24 hours</option>
            </select>
          </label>
          <button
            className="primary-button"
            disabled={Boolean(busy) || enrolled >= 5}
            type="submit"
          >
            <Plus size={15} />
            Confirm and save agent
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
                · Every {agent.cadence_hours}h
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
              {agent.last_error &&
                (!runs[agent.id] || runs[agent.id].id === agent.last_job_id) &&
                busy !== agent.id && (
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
    </section>
  );
}
