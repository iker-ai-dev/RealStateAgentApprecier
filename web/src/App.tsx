import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowUp,
  Bot,
  Building2,
  ChevronRight,
  CircleAlert,
  FileText,
  Globe2,
  LoaderCircle,
  Menu,
  MessageSquare,
  Plus,
  PlugZap,
  RefreshCw,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import {
  api,
  errorMessage,
  displayTitle,
  safeUrl,
  type AgentInput,
  type CalculatedScenario,
  type ChatResult,
  type ConversationSummary,
  type Lead,
  type ProviderStatus,
} from "./api";
import { Markdown } from "./Markdown";
import { Mcp } from "./McpConnections";
import { SearchBuilder, defaultCriteria, searchPrompt } from "./SearchBuilder";
import { AgentsWorkspace } from "./AgentsWorkspace";
import { ArtifactsWorkspace } from "./ArtifactsWorkspace";
import { streamResearch } from "./researchStream";
import { ResearchProgress } from "./ResearchProgress";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: ChatResult["sources"];
};
type Thread = {
  id?: string;
  messages: Message[];
  error?: string;
  artifactId?: string;
  unavailable?: boolean;
};
type Mode = "auto" | "research" | "ask" | "schedule";
type Progress = { key: string; stage: string; started: number; leads: Lead[] };
const emptyThread: Thread = { messages: [] };
export function messageIntent(
  message: string,
  mode: Mode,
): Exclude<Mode, "auto"> {
  if (mode !== "auto") return mode;
  if (
    /\b(schedule|monitor|watch|background agent|every\s+(?:\d+|six|twelve|twenty.four)\s+hours?|daily)\b/i.test(
      message,
    )
  )
    return "schedule";
  if (
    /\b(find|search|discover|research|look for)\b/i.test(message) &&
    /\b(properties|property|centers?|centres?|malls?|leads|listings|retail|deals|in\s+[A-Z])/i.test(
      message,
    )
  )
    return "research";
  return "ask";
}
function Notice({
  children,
  retry,
}: {
  children: React.ReactNode;
  retry?: () => void;
}) {
  return (
    <div className="notice" role="alert">
      <CircleAlert size={16} />
      <span>{children}</span>
      {retry && (
        <button
          className="icon-button"
          title="Retry"
          aria-label="Retry"
          onClick={retry}
        >
          <RefreshCw size={15} />
        </button>
      )}
    </div>
  );
}
export default function App() {
  const [section, setSection] = useState<"chats" | "artifacts" | "agents">(
    "chats",
  );
  const [path, setPath] = useState(window.location.pathname);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeId, setActiveId] = useState(
    () =>
      new URLSearchParams(window.location.search).get("conversation") || "new",
  );
  const [threads, setThreads] = useState<Record<string, Thread>>({});
  const activeThread = useRef(activeId);
  activeThread.current = activeId;
  const cache = useRef<Record<string, Thread>>({});
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyVersion, setHistoryVersion] = useState(0);
  const [providers, setProviders] = useState<ProviderStatus>();
  const [statusError, setStatusError] = useState("");
  const [statusVersion, setStatusVersion] = useState(0);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadVersion, setThreadVersion] = useState(0);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<Mode>("auto");
  const [busyKey, setBusyKey] = useState<string>();
  const [progress, setProgress] = useState<Progress>();
  const [artifactId, setArtifactId] = useState<string>();
  const [artifactVersion, setArtifactVersion] = useState(0);
  const [scenarios, setScenarios] = useState<Record<string, CalculatedScenario | undefined>>({});
  const [agentVersion, setAgentVersion] = useState(0);
  const [agentDraft, setAgentDraft] = useState<Partial<AgentInput>>();
  const [agentDraftKey, setAgentDraftKey] = useState<string>();
  const [agentError, setAgentError] = useState("");
  const [agentSaving, setAgentSaving] = useState(false);
  const [criteria, setCriteria] = useState(defaultCriteria);
  const [promptBankOpen, setPromptBankOpen] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const conversation = threads[activeId] || emptyThread;
  const ready = Boolean(providers);
  const isMcp = /^\/landing(?:\/mcp)?\/?$/.test(path);
  function updateThread(key: string, update: (thread: Thread) => Thread) {
    cache.current = {
      ...cache.current,
      [key]: update(cache.current[key] || emptyThread),
    };
    setThreads(cache.current);
  }
  function selectThread(id: string) {
    setActiveId(id);
    setSection("chats");
    setSidebarOpen(false);
    setDraft("");
    const url = new URL(window.location.href);
    if (id === "new") url.searchParams.delete("conversation");
    else url.searchParams.set("conversation", id);
    window.history.replaceState({}, "", url);
  }
  function newChat() {
    if (controller.current || agentSaving) return;
    updateThread("new", () => ({ messages: [] }));
    setAgentDraft(undefined);
    setAgentError("");
    setMode("auto");
    selectThread("new");
  }
  function openArtifact(id: string) {
    setArtifactId(id);
    setSection("artifacts");
    setSidebarOpen(false);
  }
  function adoptThread(from: string, to: string) {
    if (activeThread.current !== from) return;
    setActiveId(to);
    const url = new URL(window.location.href);
    url.searchParams.set("conversation", to);
    window.history.replaceState({}, "", url);
  }
  function navigate(next: string) {
    window.history.pushState({}, "", next);
    setPath(next);
  }
  useEffect(() => {
    const pop = () => {
      setPath(window.location.pathname);
      setActiveId(
        new URLSearchParams(window.location.search).get("conversation") ||
          "new",
      );
    };
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener("popstate", pop);
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    const pending = new AbortController();
    setStatusError("");
    api
      .status(pending.signal)
      .then((value) => {
        if (!pending.signal.aborted) setProviders(value);
      })
      .catch((error) => {
        if (!pending.signal.aborted) setStatusError(errorMessage(error));
      });
    return () => pending.abort();
  }, [statusVersion]);
  useEffect(() => {
    if (!ready) return;
    const pending = new AbortController();
    setHistoryLoading(true);
    setHistoryError("");
    api
      .conversations(pending.signal)
      .then((value) => {
        if (!pending.signal.aborted) setHistory(value.conversations);
      })
      .catch((error) => {
        if (!pending.signal.aborted) setHistoryError(errorMessage(error));
      })
      .finally(() => {
        if (!pending.signal.aborted) setHistoryLoading(false);
      });
    return () => pending.abort();
  }, [ready, historyVersion]);
  useEffect(() => {
    if (!ready || activeId === "new" || cache.current[activeId]) {
      setThreadLoading(false);
      return;
    }
    const id = activeId,
      pending = new AbortController();
    setThreadLoading(true);
    api
      .conversation(id, pending.signal)
      .then((value) => {
        if (pending.signal.aborted) return;
        if (value.id !== id)
          throw new Error("Conversation response belongs to another thread.");
        updateThread(id, () => ({
          id,
          messages: value.messages.map((message, index) => ({
            id: `${id}-${index}`,
            role: message.role,
            text: message.content,
            sources: message.sources || undefined,
          })),
        }));
      })
      .catch(async (error) => {
        if (pending.signal.aborted) return;
        try {
          const job = await api.researchResult(id, pending.signal);
          if (pending.signal.aborted) return;
          updateThread(id, () => ({
            id,
            unavailable: ["queued", "running"].includes(job.status),
            messages: job.analysis
              ? [
                  {
                    id: `${id}-report`,
                    role: "assistant",
                    text: job.analysis.answer,
                    sources: job.analysis.sources,
                  },
                ]
              : [],
            artifactId: ["completed", "partial"].includes(job.status)
              ? id
              : undefined,
            error:
              job.error ||
              (["queued", "running"].includes(job.status)
                ? "Research is still running. Refresh this conversation shortly, or check Artifacts when it completes."
                : undefined),
          }));
        } catch {
          if (!pending.signal.aborted)
            updateThread(id, () => ({
              id,
              unavailable: true,
              messages: [],
              error: errorMessage(error),
            }));
        }
      })
      .finally(() => {
        if (!pending.signal.aborted) setThreadLoading(false);
      });
    return () => pending.abort();
  }, [activeId, ready, threadVersion]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.messages.length, activeId]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (
      !ready ||
      !draft.trim() ||
      controller.current ||
      threadLoading ||
      conversation.unavailable
    )
      return;
    const message = draft.trim(),
      intent = messageIntent(message, mode),
      pending = new AbortController();
    let key = activeId;
    controller.current = pending;
    setMode("auto");
    setBusyKey(key);
    setDraft("");
    setPromptBankOpen(false);
    updateThread(key, (current) => ({
      ...current,
      error: undefined,
      messages: [
        ...current.messages,
        { id: crypto.randomUUID(), role: "user", text: message },
      ],
    }));
    try {
      if (intent === "research") {
        const job = await api.research(message, pending.signal);
        if (pending.signal.aborted) return;
        const previousKey = key;
        key = job.id;
        updateThread(key, () => ({
          id: key,
          messages: [{ id: `${key}-brief`, role: "user", text: message }],
        }));
        adoptThread(previousKey, key);
        setBusyKey(key);
        setProgress({ key, stage: job.status, started: Date.now(), leads: [] });
        const result = await streamResearch(key, pending.signal, (snapshot) => {
          if (!pending.signal.aborted)
            setProgress((current) => ({
              key,
              stage: snapshot.stage || snapshot.status,
              started:
                snapshot.started_at &&
                Number.isFinite(Date.parse(snapshot.started_at))
                  ? Date.parse(snapshot.started_at)
                  : current?.started || Date.now(),
              leads: snapshot.leads,
            }));
        });
        if (pending.signal.aborted) return;
        updateThread(key, (current) => ({
          ...current,
          artifactId: key,
          error: result.error || undefined,
          messages: [
            ...current.messages,
            ...(result.analysis
              ? [
                  {
                    id: `${key}-report`,
                    role: "assistant" as const,
                    text: result.analysis.answer,
                    sources: result.analysis.sources,
                  },
                ]
              : []),
          ],
        }));
        setArtifactVersion((value) => value + 1);
      } else if (intent === "schedule") {
        const result = await api.agentDraft(
          message,
          conversation.id,
          pending.signal,
        );
        if (pending.signal.aborted) return;
        const id = result.conversation_id || key,
          current = cache.current[key];
        updateThread(id, () => ({
          ...current,
          id: id === "new" ? undefined : id,
          messages: [
            ...current.messages,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              text: result.message,
            },
          ],
        }));
        setAgentDraft(result.draft);
        setAgentDraftKey(id);
        setAgentError(result.warnings.join(" "));
        adoptThread(key, id);
      } else {
        const result = await api.chat(
          {
            message,
            ...(scenarios[key] ? {scenario:scenarios[key]!.input} : {}),
            ...(conversation.id ? { conversation_id: conversation.id } : {}),
          },
          pending.signal,
        );
        if (pending.signal.aborted) return;
        const current = cache.current[key];
        updateThread(result.conversation_id, () => ({
          ...current,
          id: result.conversation_id,
          messages: [
            ...current.messages,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              text: result.answer,
              sources: result.sources,
            },
          ],
        }));
        adoptThread(key, result.conversation_id);
      }
      setHistoryVersion((value) => value + 1);
    } catch (error) {
      if (!pending.signal.aborted)
        updateThread(key, (current) => ({
          ...current,
          error: errorMessage(error),
        }));
    } finally {
      if (controller.current === pending) {
        controller.current = null;
        setBusyKey(undefined);
        setProgress(undefined);
      }
    }
  }
  async function confirmAgent(event: FormEvent) {
    event.preventDefault();
    if (!agentDraft || agentSaving || !agentDraft.cadence_hours) return;
    const key = activeId;
    setAgentSaving(true);
    setAgentError("");
    try {
      const agent = await api.createAgent({
        ...agentDraft,
        conversation_id: conversation.id,
      } as AgentInput);
      updateThread(key, (current) => ({
        ...current,
        messages: [
          ...current.messages,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            text: `**${agent.name}** is scheduled every **${agent.cadence_hours} hours**. Next run: ${agent.next_run_at ? new Date(agent.next_run_at).toLocaleString() : "pending"}. Reports will appear in **Artifacts**.`,
          },
        ],
      }));
      setAgentDraft(undefined);
      setAgentVersion((value) => value + 1);
      setHistoryVersion((value) => value + 1);
    } catch (error) {
      setAgentError(errorMessage(error));
    } finally {
      setAgentSaving(false);
    }
  }
  return (
    <div className="app-shell chat-first">
      <header className="app-header">
        <button
          className="icon-button sidebar-toggle"
          aria-label="Toggle sidebar"
          title="Toggle sidebar"
          aria-expanded={sidebarOpen}
          onClick={() => setSidebarOpen((value) => !value)}
        >
          <Menu size={20} />
        </button>
        <a
          className="brand"
          href="/"
          onClick={(event) => {
            event.preventDefault();
            navigate("/");
          }}
        >
          <span className="brand-mark">
            <Building2 size={19} />
          </span>
          <span>
            RetailScope <b>AI</b>
          </span>
        </a>
        <div className="header-right">
          <span className="header-label">Research workspace</span>
          <a
            href="/landing"
            className="connection-link"
            aria-label="MCP"
            title="MCP connections"
            onClick={(event) => {
              event.preventDefault();
              navigate("/landing");
            }}
          >
            <PlugZap size={17} />
            <span>MCP</span>
          </a>
        </div>
      </header>
      {isMcp && <Mcp ready={ready} onBack={() => navigate("/")} />}
      <div className="workspace-shell" hidden={isMcp}>
        <aside
          className={`workspace-sidebar ${sidebarOpen ? "is-open" : ""}`}
          aria-label="Workspace sidebar"
        >
          <button
            className="primary-button new-chat"
            disabled={Boolean(busyKey) || agentSaving}
            onClick={newChat}
          >
            <Plus size={17} />
            New chat
          </button>
          <nav aria-label="Main workspace">
            {(
              [
                ["chats", MessageSquare, "Chats"],
                ["artifacts", FileText, "Artifacts"],
                ["agents", Bot, "Agents"],
              ] as const
            ).map(([id, Icon, label]) => (
              <button
                key={id}
                aria-current={section === id ? "page" : undefined}
                onClick={() => {
                  setSection(id);
                  setSidebarOpen(false);
                }}
              >
                <Icon size={17} />
                {label}
              </button>
            ))}
          </nav>
          <div className="sidebar-heading">
            <span>Conversations</span>
            <button
              className="icon-button"
              title="Refresh conversations"
              aria-label="Refresh conversations"
              disabled={historyLoading}
              onClick={() => setHistoryVersion((value) => value + 1)}
            >
              <RefreshCw size={13} className={historyLoading ? "spin" : ""} />
            </button>
          </div>
          {historyError && (
            <Notice retry={() => setHistoryVersion((value) => value + 1)}>
              {historyError}
            </Notice>
          )}
          <div className="conversation-menu" aria-label="Past conversations">
            {historyLoading && !history.length && (
              <p className="muted">Loading conversations...</p>
            )}
            {!historyLoading && !history.length && (
              <p className="muted">No saved conversations</p>
            )}
            {history.map((thread) => (
              <button
                key={thread.id}
                disabled={Boolean(busyKey) || agentSaving}
                aria-current={
                  activeId === thread.id && section === "chats"
                    ? "page"
                    : undefined
                }
                onClick={() => selectThread(thread.id)}
                title={thread.preview}
              >
                <MessageSquare size={14} />
                <span>{displayTitle(thread.preview) || "Untitled conversation"}</span>
              </button>
            ))}
          </div>
          <div className="sidebar-footer">
            <span className={`status-dot ${ready ? "ready" : ""}`} />
            {ready
              ? "Workspace connected"
              : statusError
                ? "Connection unavailable"
                : "Connecting..."}
          </div>
        </aside>
        {sidebarOpen && (
          <button
            className="sidebar-backdrop"
            aria-label="Close sidebar"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <main className="workspace-content">
          {statusError && (
            <Notice retry={() => setStatusVersion((value) => value + 1)}>
              {statusError}
            </Notice>
          )}
          <div hidden={section !== "artifacts"}>
            <ArtifactsWorkspace
              ready={ready}
              active={!isMcp && section === "artifacts"}
              refreshKey={artifactVersion}
              selectedId={artifactId}
              onSelect={setArtifactId}
              onOpenChat={(id,scenario)=>{if(scenario)setScenarios(current=>({...current,[id]:scenario}));selectThread(id);}}
            />
          </div>
          <div hidden={section !== "agents"}>
            <AgentsWorkspace
              ready={ready}
              active={!isMcp && section === "agents"}
              refreshKey={agentVersion}
              onOpenReport={openArtifact}
              onResearchLead={(lead) => {
                setSection("chats");
                setDraft(`Analyze ${lead.title} using its source: ${lead.url}`);
                setMode("ask");
              }}
            />
          </div>
          <section
            className="chat-surface"
            aria-label="Research chat"
            hidden={section !== "chats"}
          >
            <header className="chat-surface-heading">
              <div>
                <MessageSquare size={17} />
                <h1>
                  {conversation.messages.length
                    ? "Research conversation"
                    : "New conversation"}
                </h1>
              </div>
              {conversation.id && (
                <button
                  className="icon-button"
                  title="Refresh conversation"
                  aria-label="Refresh conversation"
                  disabled={Boolean(busyKey) || threadLoading}
                  onClick={() => {
                    delete cache.current[activeId];
                    setThreadVersion((value) => value + 1);
                  }}
                >
                  <RefreshCw size={15} />
                </button>
              )}
            </header>
            <div
              className="chat-scroll"
              aria-label="Conversation"
              aria-live="polite"
            >
              {threadLoading && (
                <p className="loading-state" role="status">
                  <LoaderCircle size={18} className="spin" />
                  Loading conversation
                </p>
              )}
              {!conversation.messages.length &&
                !threadLoading &&
                !conversation.error && (
                  <div className="chat-welcome">
                    <span className="chat-emblem">
                      <Building2 size={28} />
                    </span>
                    <h2>What should we research?</h2>
                    <div className="suggestions">
                      {[
                        [
                          "Find shopping centers",
                          searchPrompt(criteria),
                          "research",
                        ],
                        [
                          "Schedule a background agent",
                          "Watch shopping centers in Phoenix, AZ from $1M to $10M every 6 hours for value-add opportunities.",
                          "schedule",
                        ],
                        [
                          "Review the evidence",
                          "What evidence should I verify before evaluating these properties?",
                          "ask",
                        ],
                      ].map(([label, prompt, intent]) => (
                        <button
                          key={label}
                          onClick={() => {
                            setDraft(prompt);
                            setMode(intent as Mode);
                            document.getElementById("chat-message")?.focus();
                          }}
                        >
                          <span>{label}</span>
                          <ChevronRight size={15} />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              {conversation.messages.map((message) => (
                <article className={`message ${message.role}`} key={message.id}>
                  <div className="message-author">
                    {message.role === "assistant" ? (
                      <Building2 size={15} />
                    ) : (
                      <MessageSquare size={15} />
                    )}{" "}
                    {message.role === "assistant" ? "RetailScope" : "You"}
                  </div>
                  <div className="markdown">
                    <Markdown>{message.text}</Markdown>
                  </div>
                  {Boolean(message.sources?.length) && (
                    <details className="message-sources">
                      <summary>{message.sources!.length} sources</summary>
                      <div className="source-links">
                        {message.sources!.map((source, index) =>
                          safeUrl(source.url) ? (
                            <a
                              key={index}
                              href={safeUrl(source.url)}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              <Globe2 size={13} />
                              {source.title}
                            </a>
                          ) : (
                            <span key={index}>{source.title}</span>
                          ),
                        )}
                      </div>
                    </details>
                  )}
                </article>
              ))}
              {progress?.key === activeId && (
                <>
                  <ResearchProgress
                    stage={progress.stage}
                    startedAt={progress.started}
                    count={progress.leads.length}
                  />
                  {progress.leads.length > 0 && (
                    <details className="stream-sources">
                      <summary>
                        {progress.leads.length} retrieved sources
                      </summary>
                      {progress.leads.map((lead) => (
                        <a
                          key={lead.id}
                          href={safeUrl(lead.url)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {lead.title}
                        </a>
                      ))}
                    </details>
                  )}
                </>
              )}
              {busyKey === activeId && !progress && (
                <p className="chat-pending" role="status">
                  <LoaderCircle size={16} className="spin" />
                  Preparing your response
                </p>
              )}
              {conversation.error && <Notice>{conversation.error}</Notice>}
              {conversation.artifactId && (
                <button
                  className="artifact-link"
                  onClick={() => openArtifact(conversation.artifactId!)}
                >
                  <FileText size={20} />
                  <span>
                    <strong>Research document</strong>
                    <small>Analysis, property facts and sources</small>
                  </span>
                  <ChevronRight size={17} />
                </button>
              )}
              {agentDraft && agentDraftKey === activeId && (
                <form className="agent-draft" onSubmit={confirmAgent}>
                  <header>
                    <Bot size={19} />
                    <h2>Review background agent</h2>
                  </header>
                  <label>
                    Name
                    <input
                      required
                      maxLength={80}
                      value={agentDraft.name || ""}
                      onChange={(event) =>
                        setAgentDraft({
                          ...agentDraft,
                          name: event.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    City
                    <input
                      required
                      value={agentDraft.city || ""}
                      placeholder="Phoenix, AZ"
                      onChange={(event) =>
                        setAgentDraft({
                          ...agentDraft,
                          city: event.target.value,
                        })
                      }
                    />
                  </label>
                  <div className="budget-fields">
                    {(["min_price", "max_price"] as const).map((key, index) => (
                      <label key={key}>
                        {index ? "Max budget ($M)" : "Min budget ($M)"}
                        <input
                          type="number"
                          required
                          min={index ? 0.1 : 0}
                          max={1000}
                          step="0.1"
                          value={
                            agentDraft[key] === undefined
                              ? ""
                              : agentDraft[key]! / 1000000
                          }
                          onChange={(event) =>
                            setAgentDraft({
                              ...agentDraft,
                              [key]:
                                event.target.value === ""
                                  ? undefined
                                  : Math.round(
                                      event.target.valueAsNumber * 1000000,
                                    ),
                            })
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <label>
                    Strategy
                    <input
                      required
                      value={agentDraft.strategy || ""}
                      placeholder="Value-add"
                      onChange={(event) =>
                        setAgentDraft({
                          ...agentDraft,
                          strategy: event.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    Schedule
                    <select
                      required
                      value={agentDraft.cadence_hours || ""}
                      onChange={(event) =>
                        setAgentDraft({
                          ...agentDraft,
                          cadence_hours: event.target.value
                            ? (Number(event.target.value) as 6 | 12 | 24)
                            : undefined,
                        })
                      }
                    >
                      <option value="" disabled>
                        Choose a schedule
                      </option>
                      <option value={6}>Every 6 hours</option>
                      <option value={12}>Every 12 hours</option>
                      <option value={24}>Every day</option>
                    </select>
                  </label>
                  <p className="muted">
                    Scheduled searches use your connected provider credits.
                    Active until this workspace expires.
                  </p>
                  {agentError && <Notice>{agentError}</Notice>}
                  <div className="form-actions">
                    <button
                      className="text-button"
                      type="button"
                      disabled={agentSaving}
                      onClick={() => setAgentDraft(undefined)}
                    >
                      Discard
                    </button>
                    <button
                      className="primary-button"
                      disabled={
                        agentSaving ||
                        !agentDraft.cadence_hours ||
                        !ready ||
                        (agentDraft.max_price ?? 0) <=
                          (agentDraft.min_price ?? 0)
                      }
                    >
                      {agentSaving ? (
                        <LoaderCircle size={15} className="spin" />
                      ) : (
                        <Plus size={15} />
                      )}
                      Create agent
                    </button>
                  </div>
                </form>
              )}
              <div ref={bottom} />
            </div>
            <div className="chat-composer-wrap">
            {scenarios[activeId] && <div className="scenario-attachment"><FileText size={14}/><span>Financial scenario attached</span><button className="icon-button" title="Remove scenario" aria-label="Remove scenario" onClick={()=>setScenarios(current=>({...current,[activeId]:undefined}))}><X size={14}/></button></div>}
              {promptBankOpen && (
                <div className="prompt-builder">
                  <div className="sidebar-heading">
                    <h2>Search preferences</h2>
                    <button
                      className="icon-button"
                      title="Close preferences"
                      aria-label="Close preferences"
                      onClick={() => setPromptBankOpen(false)}
                    >
                      <X size={16} />
                    </button>
                  </div>
                  <SearchBuilder value={criteria} onChange={setCriteria} />
                  <button
                    className="text-button"
                    onClick={() => {
                      setDraft(searchPrompt(criteria));
                      setMode("research");
                      setPromptBankOpen(false);
                    }}
                  >
                    Use research prompt
                    <ChevronRight size={14} />
                  </button>
                </div>
              )}
              <form className="composer" onSubmit={send}>
                <label className="sr-only" htmlFor="chat-message">
                  Message RetailScope
                </label>
                <textarea
                  id="chat-message"
                  disabled={
                    Boolean(busyKey) ||
                    agentSaving ||
                    threadLoading ||
                    conversation.unavailable
                  }
                  rows={3}
                  maxLength={2000}
                  placeholder="Ask about a property, find leads, or schedule an agent..."
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <div className="composer-bottom">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Search preferences"
                    title="Search preferences"
                    aria-expanded={promptBankOpen}
                    onClick={() => setPromptBankOpen((value) => !value)}
                  >
                    <SlidersHorizontal size={17} />
                  </button>
                  <select
                    className="composer-mode"
                    aria-label="Message mode"
                    value={mode}
                    onChange={(event) => setMode(event.target.value as Mode)}
                  >
                    <option value="auto">Auto</option>
                    <option value="research">Find leads</option>
                    <option value="ask">Ask</option>
                    <option value="schedule">Schedule agent</option>
                  </select>
                  <span className="model-label">{providers?.model || ""}</span>
                  {busyKey ? (
                    <button
                      type="button"
                      className="icon-button"
                      title="Stop waiting"
                      aria-label="Stop waiting"
                      onClick={() => {
                        controller.current?.abort();
                        setBusyKey(undefined);
                        setProgress(undefined);
                        updateThread(activeId, (current) => ({
                          ...current,
                          error:
                            "Stopped waiting. A submitted search may still finish; its report will appear in Artifacts.",
                        }));
                      }}
                    >
                      <Square size={17} />
                    </button>
                  ) : (
                    <button
                      className="send-button"
                      type="submit"
                      aria-label="Send message"
                      title="Send message"
                      disabled={
                        !ready ||
                        !draft.trim() ||
                        threadLoading ||
                        conversation.unavailable
                      }
                    >
                      <ArrowUp size={19} />
                    </button>
                  )}
                </div>
              </form>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
