import { useEffect, useRef, useState, type FormEvent } from "react";
import { requestSignal } from "./requestSignal";
import {
  ArrowDown,
  ArrowUp,
  Building2,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  FileSearch,
  Globe2,
  History,
  LoaderCircle,
  MapPin,
  MessageSquare,
  Plus,
  PlugZap,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import {
  api,
  errorMessage,
  pollResearch,
  safeUrl,
  type CalculatedScenario,
  type ChatResult,
  type ConversationSummary,
  type Lead,
  type ProviderStatus,
} from "./api";
import { Markdown } from "./Markdown";
import { Mcp } from "./McpConnections";
import { ScenarioForm } from "./ScenarioForm";
import { SearchBuilder, defaultCriteria, searchPrompt } from "./SearchBuilder";
import { LeadDetails } from "./LeadDetails";
import { AgentsWorkspace } from "./AgentsWorkspace";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: ChatResult["sources"];
  model?: string;
  usage?: unknown;
  scenarioAttached?: boolean;
};
type Conversation = { messages: Message[]; id?: string; error?: string };
type View = "leads" | "chat" | "details";
const blankConversation: Conversation = { messages: [] };

function Empty({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Search;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon size={28} strokeWidth={1.5} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

function Notice({
  children,
  onRetry,
}: {
  children: React.ReactNode;
  onRetry?: () => void;
}) {
  return (
    <div className="notice" role="alert">
      <CircleAlert size={16} />
      <span>{children}</span>
      {onRetry && (
        <button
          className="icon-button"
          onClick={onRetry}
          aria-label="Retry"
          title="Retry"
        >
          <RefreshCw size={16} />
        </button>
      )}
    </div>
  );
}

export default function App() {
  const [section, setSection] = useState<"chat" | "agents">("chat");
  const [criteria, setCriteria] = useState(defaultCriteria);
  const [path, setPath] = useState(window.location.pathname);
  const [providers, setProviders] = useState<ProviderStatus>();
  const [statusError, setStatusError] = useState("");
  const [statusVersion, setStatusVersion] = useState(0);
  const [bootstrapReady, setBootstrapReady] = useState(false);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadsLoading, setLeadsLoading] = useState(true);
  const [leadsError, setLeadsError] = useState("");
  const [leadsVersion, setLeadsVersion] = useState(0);
  const [selectedId, setSelectedId] = useState<string>();
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState(searchPrompt(defaultCriteria));
  const [researchStatus, setResearchStatus] = useState("");
  const [researchBusy, setResearchBusy] = useState(false);
  const [researchError, setResearchError] = useState("");
  const [researchFinished, setResearchFinished] = useState(false);
  const [view, setView] = useState<View>("chat");
  const [draft, setDraft] = useState("");
  const [conversations, setConversations] = useState<
    Record<string, Conversation>
  >({});
  const [chatBusyKey, setChatBusyKey] = useState<string>();
  const [propertyTab, setPropertyTab] = useState<"overview" | "scenario">(
    "overview",
  );
  const [scenarios, setScenarios] = useState<
    Record<string, CalculatedScenario | undefined>
  >({});
  const [attached, setAttached] = useState<Record<string, boolean>>({});
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [restoreId, setRestoreId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("conversation"),
  );
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadVersion, setThreadVersion] = useState(0);
  const researchController = useRef<AbortController | null>(null);
  const chatController = useRef<AbortController | null>(null);
  const leadsController = useRef<AbortController | null>(null);
  const chatBottom = useRef<HTMLDivElement>(null);
  const selected = leads.find((lead) => lead.id === selectedId);
  const contextKey = selected?.id || "__general__";
  const activeContext = useRef(contextKey);
  activeContext.current = contextKey;
  const conversation = conversations[contextKey] || blankConversation;
  const filtered = leads.filter((lead) =>
    `${lead.title} ${lead.location || ""} ${lead.summary || ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  const isMcp = ["/landing", "/landing/mcp"].includes(path.replace(/\/$/, ""));

  useEffect(() => {
    const pop = () => {
      setPath(window.location.pathname);
      setRestoreId(
        new URLSearchParams(window.location.search).get("conversation"),
      );
      setThreadVersion((value) => value + 1);
    };
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener("popstate", pop);
      researchController.current?.abort();
      chatController.current?.abort();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStatusError("");
    api
      .status(controller.signal)
      .then((status) => {
        if (!controller.signal.aborted) {
          setProviders(status);
          setBootstrapReady(true);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setProviders(undefined);
          setStatusError(errorMessage(error));
          setLeadsLoading(false);
        }
      });
    return () => controller.abort();
  }, [statusVersion]);
  useEffect(() => {
    if (!bootstrapReady) return;
    const controller = new AbortController();
    leadsController.current = controller;
    setLeadsLoading(true);
    setLeadsError("");
    api
      .leads(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setLeads(result.leads);
        setLeadsLoading(false);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setLeadsError(errorMessage(error));
          setLeadsLoading(false);
        }
      });
    return () => controller.abort();
  }, [leadsVersion, bootstrapReady]);
  useEffect(() => {
    if (!bootstrapReady) return;
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError("");
    api
      .conversations(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setHistory(result.conversations);
        setHistoryLoading(false);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setHistoryError(errorMessage(error));
          setHistoryLoading(false);
        }
      });
    return () => controller.abort();
  }, [historyVersion, bootstrapReady]);
  useEffect(() => {
    if (!restoreId || !bootstrapReady) {
      setThreadLoading(false);
      return;
    }
    const controller = new AbortController();
    setThreadLoading(true);
    setHistoryError("");
    setSelectedId(undefined);
    setSection("chat");
    setView("chat");
    updateConversation("__general__", () => ({ messages: [] }));
    api
      .conversation(restoreId, controller.signal)
      .then((thread) => {
        if (controller.signal.aborted) return;
        if (thread.id !== restoreId)
          throw new Error("Conversation response belongs to another thread.");
        updateConversation("__general__", () => ({
          id: thread.id,
          messages: thread.messages.map((message, index) => ({
            id: `${thread.id}-${index}`,
            role: message.role,
            text: message.content,
            sources: message.sources ?? undefined,
            model: message.model ?? undefined,
            usage: message.usage,
          })),
        }));
        setThreadLoading(false);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setHistoryError(errorMessage(error));
          setThreadLoading(false);
        }
      });
    return () => controller.abort();
  }, [restoreId, threadVersion, bootstrapReady]);
  useEffect(() => {
    chatBottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.messages.length, chatBusyKey, view]);

  function navigate(next: string) {
    const url = new URL(next, window.location.origin);
    if (conversation.id) url.searchParams.set("conversation", conversation.id);
    window.history.pushState({}, "", url);
    setPath(next);
  }
  function threadUrl(id?: string) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("conversation", id);
    else url.searchParams.delete("conversation");
    window.history.replaceState({}, "", url);
  }
  function updateConversation(
    key: string,
    update: (value: Conversation) => Conversation,
  ) {
    setConversations((current) => ({
      ...current,
      [key]: update(current[key] || blankConversation),
    }));
  }

  async function research(event: FormEvent | null, existingId?: string) {
    event?.preventDefault();
    if (existingId) {
      researchController.current?.abort();
      researchController.current = null;
      chatController.current?.abort();
      setSection("chat");
      setView("chat");
      setSelectedId(undefined);
      setRestoreId(null);
      updateConversation("__general__", () => ({ messages: [] }));
    }
    if (
      !bootstrapReady ||
      (!existingId && !query.trim()) ||
      researchController.current
    )
      return;
    leadsController.current?.abort();
    setLeadsLoading(false);
    const controller = new AbortController();
    researchController.current = controller;
    const pending = requestSignal(controller.signal, 330_000);
    const signal = pending.signal;
    setResearchBusy(true);
    setResearchFinished(false);
    setResearchError("");
    setResearchStatus("Submitting");
    try {
      const job = existingId
        ? { id: existingId, status: "Loading report" }
        : await api.research(query.trim(), signal);
      setResearchStatus(job.status);
      const result = await pollResearch(job.id, signal, setResearchStatus);
      if (!controller.signal.aborted) {
        setLeads(result.leads);
        setLeadsError("");
        setSelectedId(undefined);
        setResearchFinished(true);
        const partial = result.status.toLowerCase() === "partial";
        setResearchStatus(
          partial ? "Sources saved; analysis unavailable" : "Completed",
        );
        if (partial) {
          setResearchError(result.error || "");
          setView("leads");
        }
        setFilter("");
        if (!partial && result.analysis) {
          updateConversation("__general__", () => ({
            id: job.id,
            messages: [
              ...(!existingId
                ? [
                    {
                      id: `${job.id}-brief`,
                      role: "user" as const,
                      text: query.trim(),
                    },
                  ]
                : []),
              {
                id: `${job.id}-report`,
                role: "assistant",
                text: result.analysis!.answer,
                sources: result.analysis!.sources,
                model: result.analysis!.model,
              },
            ],
          }));
          threadUrl(job.id);
          setHistoryVersion((version) => version + 1);
          setView("chat");
        }
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setResearchError(
          signal.aborted
            ? "Research timed out. Please try again later."
            : errorMessage(error),
        );
    } finally {
      pending.dispose();
      if (researchController.current === controller) {
        researchController.current = null;
        setResearchBusy(false);
      }
    }
  }

  function openReport(id: string) {
    researchController.current?.abort();
    chatController.current?.abort();
    setDraft("");
    setSelectedId(undefined);
    setSection("chat");
    setView("chat");
    threadUrl(id);
    setRestoreId(id);
    setThreadVersion((value) => value + 1);
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (
      !bootstrapReady ||
      threadLoading ||
      !draft.trim() ||
      chatController.current
    )
      return;
    const key = contextKey;
    const message = draft.trim();
    const controller = new AbortController();
    chatController.current = controller;
    setDraft("");
    setChatBusyKey(key);
    const scenario = attached[key] ? scenarios[key]?.input : undefined;
    updateConversation(key, (current) => ({
      ...current,
      error: undefined,
      messages: [
        ...current.messages,
        {
          id: crypto.randomUUID(),
          role: "user",
          text: message,
          scenarioAttached: Boolean(scenario),
        },
      ],
    }));
    try {
      const result = await api.chat(
        {
          message,
          ...(selected ? { lead_id: selected.id } : {}),
          ...(conversation.id ? { conversation_id: conversation.id } : {}),
          ...(scenario ? { scenario } : {}),
        },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        updateConversation(key, (current) => ({
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
        setHistoryVersion((value) => value + 1);
        if (activeContext.current === key) threadUrl(result.conversation_id);
      }
    } catch (error) {
      if (!controller.signal.aborted)
        updateConversation(key, (current) => ({
          ...current,
          error: errorMessage(error),
        }));
    } finally {
      if (chatController.current === controller) {
        chatController.current = null;
        setChatBusyKey(undefined);
      }
    }
  }

  const providerPills = (
    <div
      className="provider-pills"
      aria-label="Provider credential configuration"
    >
      {(["snowflake", "nimble"] as const).map((provider) => (
        <span
          key={provider}
          className={`provider ${providers ? (providers.providers[provider] ? "connected" : "disconnected") : "unknown"}`}
          title={`${provider}: ${providers ? (providers.providers[provider] ? "Credentials configured; network health not checked" : "Credentials not configured") : "Configuration unavailable"}`}
        >
          <span className="status-dot" />
          {provider === "snowflake" ? "Snowflake" : "Nimble"}
          <span className="sr-only">
            {providers
              ? providers.providers[provider]
                ? " credentials configured"
                : " credentials not configured"
              : " configuration unknown"}
          </span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="app-shell">
      <header className="app-header">
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
        <span className="header-divider" />
        <span className="header-label">Research workspace</span>
        <div className="header-right">
          {providerPills}
          <a
            className={`connection-link ${isMcp ? "active" : ""}`}
            href="/landing"
            onClick={(event) => {
              event.preventDefault();
              navigate("/landing");
            }}
            title="MCP connections"
            aria-label="MCP"
          >
            <PlugZap size={17} />
            <span>MCP</span>
          </a>
        </div>
      </header>
      {isMcp && statusError && (
        <Notice onRetry={() => setStatusVersion((value) => value + 1)}>
          API session unavailable. Please retry.
        </Notice>
      )}
      {isMcp ? (
        <Mcp ready={bootstrapReady} onBack={() => navigate("/")} />
      ) : (
        <>
          {statusError && (
            <div className="status-notice">
              <CircleAlert size={14} />
              <span>Provider configuration unavailable</span>
              <button
                className="text-button"
                title={statusError}
                onClick={() => setStatusVersion((value) => value + 1)}
              >
                Retry
              </button>
            </div>
          )}
          <nav className="workspace-nav" aria-label="Main workspace">
            <button
              aria-current={section === "chat" ? "page" : undefined}
              onClick={() => setSection("chat")}
            >
              <MessageSquare size={16} />
              Chat
            </button>
            <button
              aria-current={section === "agents" ? "page" : undefined}
              onClick={() => setSection("agents")}
            >
              <History size={16} />
              Background agents / Inbox
            </button>
          </nav>
          <div hidden={section !== "agents"}>
            <AgentsWorkspace
              ready={bootstrapReady}
              active={section === "agents"}
              onOpenReport={(id) => {
                void research(null, id);
              }}
              onResearchLead={(lead) => {
                setLeads((current) => [
                  lead,
                  ...current.filter((item) => item.id !== lead.id),
                ]);
                setSelectedId(lead.id);
                setSection("chat");
                setView("chat");
                setDraft("");
              }}
            />
          </div>
          <div hidden={section !== "chat"}>
            <nav className="mobile-nav" aria-label="Workspace view">
              {(
                [
                  ["leads", Search, "Build search"],
                  ["chat", MessageSquare, "Chat"],
                  ["details", Building2, "Property"],
                ] as const
              ).map(([id, Icon, label]) => (
                <button
                  key={id}
                  aria-current={view === id ? "page" : undefined}
                  onClick={() => setView(id)}
                >
                  <Icon size={16} />
                  {label}
                </button>
              ))}
            </nav>
            <main className="workspace">
              <section
                className={`leads-pane pane ${view === "leads" ? "mobile-active" : ""}`}
                aria-label="Lead discovery"
              >
                <div className="pane-heading">
                  <div>
                    <span className="eyebrow">RESEARCH</span>
                    <h1>
                      Build search <span className="count">{leads.length}</span>
                    </h1>
                  </div>
                  <button
                    className="icon-button"
                    title="Refresh leads"
                    aria-label="Refresh leads"
                    disabled={leadsLoading || researchBusy}
                    onClick={() => {
                      setResearchFinished(false);
                      setLeadsVersion((value) => value + 1);
                    }}
                  >
                    <RefreshCw
                      size={16}
                      className={leadsLoading ? "spin" : ""}
                    />
                  </button>
                </div>
                <form className="research-form" onSubmit={research}>
                  <SearchBuilder
                    value={criteria}
                    disabled={researchBusy}
                    onChange={(next) => {
                      setCriteria(next);
                      setQuery(searchPrompt(next));
                    }}
                  />
                  <details className="edit-prompt">
                    <summary>Edit prompt</summary>
                    <label className="sr-only" htmlFor="research-query">
                      Research brief
                    </label>
                    <textarea
                      id="research-query"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Retail centers in Phoenix with redevelopment potential..."
                      rows={3}
                      disabled={researchBusy}
                      minLength={3}
                      maxLength={2000}
                    />
                  </details>
                  <div className="research-actions">
                    <span>
                      <Globe2 size={13} />
                      Public-source research
                    </span>
                    {researchBusy ? (
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => {
                          researchController.current?.abort();
                          setResearchStatus("Cancelled");
                          setResearchError(
                            "Research cancelled. No new results were loaded.",
                          );
                        }}
                      >
                        <Square size={13} />
                        Cancel
                      </button>
                    ) : (
                      <button
                        className="primary-button"
                        type="submit"
                        disabled={!bootstrapReady || query.trim().length < 3}
                      >
                        <Search size={15} />
                        Send prompt
                      </button>
                    )}
                  </div>
                </form>
                {researchBusy && (
                  <div className="progress" role="status">
                    <LoaderCircle size={16} className="spin" />
                    <span>
                      Researching <b>{researchStatus.replaceAll("_", " ")}</b>
                    </span>
                  </div>
                )}
                {!researchBusy &&
                  researchFinished &&
                  researchStatus === "Sources saved; analysis unavailable" && (
                    <div className="progress" role="status">
                      <span>{researchStatus}</span>
                    </div>
                  )}
                {researchError && (
                  <Notice onRetry={() => setLeadsVersion((value) => value + 1)}>
                    {researchError}
                  </Notice>
                )}
                {leadsError && (
                  <Notice onRetry={() => setLeadsVersion((value) => value + 1)}>
                    {leadsError}
                  </Notice>
                )}
                <div className="lead-filter">
                  <Search size={15} />
                  <input
                    aria-label="Filter leads"
                    placeholder="Filter leads"
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  />
                  {filter && (
                    <button
                      className="icon-button"
                      title="Clear filter"
                      aria-label="Clear filter"
                      onClick={() => setFilter("")}
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
                <div
                  className="lead-list"
                  aria-busy={leadsLoading || researchBusy}
                >
                  {leadsLoading ? (
                    <div className="loading-state" role="status">
                      <LoaderCircle size={22} className="spin" />
                      Loading leads
                    </div>
                  ) : filtered.length ? (
                    filtered.map((lead) => (
                      <button
                        className={`lead-row ${selected?.id === lead.id ? "selected" : ""}`}
                        key={lead.id}
                        aria-pressed={selected?.id === lead.id}
                        onClick={() => {
                          setSelectedId(lead.id);
                          setView("details");
                          setDraft("");
                        }}
                      >
                        <div className="lead-row-top">
                          <span className="lead-icon">
                            <Building2 size={16} />
                          </span>
                          <span className="lead-status">
                            {lead.status || "Source lead"}
                          </span>
                          <ChevronRight size={15} />
                        </div>
                        <h3>{lead.title}</h3>
                        {lead.location && (
                          <p className="lead-location">
                            <MapPin size={12} />
                            {lead.location}
                          </p>
                        )}
                        {lead.summary && (
                          <p className="lead-summary">{lead.summary}</p>
                        )}
                      </button>
                    ))
                  ) : (
                    <Empty
                      icon={FileSearch}
                      title={
                        filter
                          ? "No matching leads"
                          : researchFinished
                            ? "No leads returned"
                            : "Your next opportunity starts here"
                      }
                    >
                      {filter
                        ? "Try another filter."
                        : researchFinished
                          ? "Refine your research brief and search again."
                          : leadsError
                            ? "Leads will appear when the API is available."
                            : "No leads have been returned yet."}
                    </Empty>
                  )}
                </div>
                <div className="pane-footer">
                  <ShieldCheck size={13} />
                  Source leads require verification
                </div>
              </section>
              <section
                className={`chat-pane pane ${view === "chat" ? "mobile-active" : ""}`}
                aria-label="Research chat"
              >
                <div className="pane-heading">
                  <div>
                    <span className="eyebrow cyan">RESEARCH</span>
                    <h2>Ask RetailScope</h2>
                  </div>
                  <div className="chat-tools">
                    <div className="history-picker">
                      <History size={15} />
                      <select
                        aria-label="Chat history"
                        value={conversation.id || ""}
                        disabled={
                          Boolean(chatBusyKey) ||
                          historyLoading ||
                          threadLoading
                        }
                        onChange={(event) => {
                          setDraft("");
                          const id = event.target.value;
                          threadUrl(id || undefined);
                          if (id) openReport(id);
                          else {
                            setRestoreId(null);
                            updateConversation(contextKey, () => ({
                              messages: [],
                            }));
                          }
                        }}
                      >
                        <option value="">
                          {historyLoading ? "Loading history" : "Chat history"}
                        </option>
                        {conversation.id &&
                          !history.some(
                            (thread) => thread.id === conversation.id,
                          ) && (
                            <option value={conversation.id}>
                              Current conversation
                            </option>
                          )}
                        {history.map((thread) => (
                          <option key={thread.id} value={thread.id}>
                            {thread.preview.slice(0, 70) ||
                              "Saved conversation"}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      className="icon-button"
                      title="New conversation"
                      aria-label="New conversation"
                      disabled={
                        Boolean(chatBusyKey) ||
                        threadLoading ||
                        (!conversation.messages.length && !restoreId)
                      }
                      onClick={() => {
                        setRestoreId(null);
                        threadUrl();
                        updateConversation(contextKey, () => ({
                          messages: [],
                        }));
                        setDraft("");
                      }}
                    >
                      <Plus size={18} />
                    </button>
                  </div>
                </div>
                <div className="chat-context">
                  <span className="context-icon">
                    <MessageSquare size={14} />
                  </span>
                  <span>{selected ? selected.title : "Ad hoc research"}</span>
                  {selected && (
                    <button
                      className="icon-button"
                      title="Return to ad hoc chat"
                      aria-label="Return to ad hoc chat"
                      onClick={() => {
                        setSelectedId(undefined);
                        setDraft("");
                      }}
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
                <div
                  className="chat-history"
                  aria-label="Conversation"
                  aria-live="polite"
                >
                  {researchBusy && (
                    <div className="chat-pending" role="status">
                      <LoaderCircle size={16} className="spin" />
                      {researchStatus.replaceAll("_", " ")}
                    </div>
                  )}
                  {historyError && (
                    <Notice
                      onRetry={() => {
                        if (restoreId) setThreadVersion((value) => value + 1);
                        else setHistoryVersion((value) => value + 1);
                      }}
                    >
                      {historyError}
                    </Notice>
                  )}
                  {threadLoading && (
                    <div className="loading-state" role="status">
                      <LoaderCircle size={20} className="spin" />
                      Loading conversation
                    </div>
                  )}
                  {!conversation.messages.length && !threadLoading && (
                    <div className="chat-welcome">
                      <span className="chat-emblem">
                        <Sparkles size={28} strokeWidth={1.5} />
                      </span>
                      <h2>What are you looking into?</h2>
                      <p>
                        {selected
                          ? "Explore the selected property and its source evidence."
                          : "Ask a property question or investigate a market."}
                      </p>
                      <div className="suggestions">
                        {[
                          "What evidence should I verify?",
                          "Which risks need more research?",
                        ].map((prompt) => (
                          <button
                            key={prompt}
                            onClick={() => {
                              setDraft(prompt);
                              document.getElementById("chat-message")?.focus();
                            }}
                          >
                            <span>{prompt}</span>
                            <ArrowUp size={14} />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {conversation.messages.map((message) => (
                    <article
                      className={`message ${message.role}`}
                      key={message.id}
                    >
                      <div className="message-author">
                        {message.role === "assistant" ? (
                          <Sparkles size={14} />
                        ) : (
                          <MessageSquare size={14} />
                        )}
                        {message.role === "assistant"
                          ? "RetailScope AI"
                          : "You"}
                        {message.model && (
                          <span className="message-model">{message.model}</span>
                        )}
                        {message.scenarioAttached && (
                          <span className="message-model">
                            Scenario attached
                          </span>
                        )}
                      </div>
                      <div className="markdown">
                        <Markdown>{message.text}</Markdown>
                      </div>
                      {message.sources && message.sources.length > 0 && (
                        <div className="source-links">
                          <span>Sources</span>
                          {message.sources.map((source, index) =>
                            safeUrl(source.url) ? (
                              <a
                                href={safeUrl(source.url)}
                                target="_blank"
                                rel="noopener noreferrer"
                                key={`${source.url}-${index}`}
                              >
                                <Globe2 size={12} />
                                {source.title}
                                <ExternalLink size={11} />
                              </a>
                            ) : (
                              <span key={index}>
                                {source.title} (link unavailable)
                              </span>
                            ),
                          )}
                        </div>
                      )}
                    </article>
                  ))}
                  {chatBusyKey === contextKey && (
                    <div className="chat-pending" role="status">
                      <LoaderCircle size={16} className="spin" />
                      Preparing an answer
                    </div>
                  )}
                  {conversation.error && <Notice>{conversation.error}</Notice>}
                  <div ref={chatBottom} />
                </div>
                <form className="composer" onSubmit={send}>
                  <label className="sr-only" htmlFor="chat-message">
                    Message RetailScope
                  </label>
                  <textarea
                    id="chat-message"
                    placeholder="Ask about a property, market or research question..."
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    maxLength={4000}
                    rows={2}
                    disabled={
                      !bootstrapReady || Boolean(chatBusyKey) || threadLoading
                    }
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
                  {scenarios[contextKey] && (
                    <label className="scenario-attachment">
                      <input
                        type="checkbox"
                        checked={attached[contextKey] || false}
                        disabled={Boolean(chatBusyKey)}
                        onChange={(event) =>
                          setAttached((current) => ({
                            ...current,
                            [contextKey]: event.target.checked,
                          }))
                        }
                      />
                      Attach calculated scenario
                    </label>
                  )}
                  <div className="composer-bottom">
                    <span className="model-label">
                      <span className="status-dot" />
                      {providers?.model || "Model unavailable"}
                    </span>
                    {chatBusyKey === contextKey ? (
                      <button
                        type="button"
                        className="send-button"
                        title="Stop request"
                        aria-label="Stop request"
                        onClick={() => {
                          chatController.current?.abort();
                          updateConversation(contextKey, (current) => ({
                            ...current,
                            error: "Request cancelled. No answer was received.",
                          }));
                        }}
                      >
                        <Square size={15} />
                      </button>
                    ) : (
                      <button
                        className="send-button"
                        type="submit"
                        title="Send message"
                        aria-label="Send message"
                        disabled={
                          !bootstrapReady ||
                          !draft.trim() ||
                          Boolean(chatBusyKey) ||
                          threadLoading
                        }
                      >
                        <ArrowUp size={18} />
                      </button>
                    )}
                  </div>
                </form>
              </section>
              <aside
                hidden={!selected}
                className={`details-pane pane ${view === "details" ? "mobile-active" : ""}`}
                aria-label="Selected property"
              >
                <div className="pane-heading">
                  <div>
                    <span className="eyebrow">PROPERTY</span>
                    <h2>Property details</h2>
                  </div>
                  {selected && (
                    <button
                      className="icon-button"
                      title="Deselect property"
                      aria-label="Deselect property"
                      onClick={() => setSelectedId(undefined)}
                    >
                      <X size={16} />
                    </button>
                  )}
                </div>
                {selected ? (
                  <div className="property-content">
                    <div
                      role="tablist"
                      aria-label="Property detail view"
                      className="property-tabs"
                    >
                      {(["overview", "scenario"] as const).map((tab) => (
                        <button
                          key={tab}
                          role="tab"
                          id={`property-tab-${tab}`}
                          aria-selected={propertyTab === tab}
                          aria-controls={`property-${tab}`}
                          tabIndex={propertyTab === tab ? 0 : -1}
                          onClick={() => setPropertyTab(tab)}
                          onKeyDown={(event) => {
                            if (
                              [
                                "ArrowLeft",
                                "ArrowRight",
                                "Home",
                                "End",
                              ].includes(event.key)
                            ) {
                              event.preventDefault();
                              const next =
                                event.key === "Home"
                                  ? "overview"
                                  : event.key === "End"
                                    ? "scenario"
                                    : tab === "overview"
                                      ? "scenario"
                                      : "overview";
                              setPropertyTab(next);
                              document
                                .getElementById(`property-tab-${next}`)
                                ?.focus();
                            }
                          }}
                        >
                          {tab === "overview" ? "Overview" : "Underwriting"}
                        </button>
                      ))}
                    </div>
                    <div
                      id="property-overview"
                      role="tabpanel"
                      aria-labelledby="property-tab-overview"
                      hidden={propertyTab !== "overview"}
                    >
                      <LeadDetails lead={selected} />
                      <button
                        className="text-button property-chat"
                        onClick={() => {
                          setView("chat");
                          document.getElementById("chat-message")?.focus();
                        }}
                      >
                        <MessageSquare size={15} />
                        Research this property
                        <ArrowDown size={14} />
                      </button>
                    </div>
                    <div
                      id="property-scenario"
                      role="tabpanel"
                      aria-labelledby="property-tab-scenario"
                      hidden={propertyTab !== "scenario"}
                    >
                      <h2 className="scenario-property-title">
                        {selected.title}
                      </h2>
                      <ScenarioForm
                        key={selected.id}
                        calculated={scenarios[selected.id]}
                        onCalculated={(scenario) =>
                          setScenarios((current) => ({
                            ...current,
                            [selected.id]: scenario,
                          }))
                        }
                        onInvalidate={() => {
                          setScenarios((current) => ({
                            ...current,
                            [selected.id]: undefined,
                          }));
                          setAttached((current) => ({
                            ...current,
                            [selected.id]: false,
                          }));
                        }}
                        onAsk={() => {
                          setAttached((current) => ({
                            ...current,
                            [selected.id]: true,
                          }));
                          setDraft(
                            "Interpret this calculated scenario. Which assumptions and risks should I verify?",
                          );
                          setView("chat");
                        }}
                      />
                    </div>
                  </div>
                ) : (
                  <Empty icon={Building2} title="No property selected">
                    Select a lead to review its details and original source.
                  </Empty>
                )}
              </aside>
            </main>
          </div>
        </>
      )}
    </div>
  );
}
