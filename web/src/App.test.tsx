import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import App, { messageIntent } from "./App";
import { api, type Agent, type Lead, type ResearchResult, type ChatResult } from "./api";
import { Markdown } from "./Markdown";
import { LeadDetails } from "./LeadDetails";
import * as streams from "./researchStream";

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.spyOn(api, "status").mockResolvedValue({
    providers: { snowflake: true, nimble: true },
    model: "API model",
  });
  vi.spyOn(api, "conversations").mockResolvedValue({ conversations: [] });
  vi.spyOn(api, "runs").mockResolvedValue({ runs: [] });
  vi.spyOn(api, "agents").mockResolvedValue({ agents: [] });
  vi.spyOn(api, "agentRuns").mockResolvedValue({ runs: [] });
  vi.spyOn(api, "artifacts").mockResolvedValue({ artifacts: [] });
});
const agent: Agent = {
  id: "a1",
  name: "Phoenix watch",
  city: "Phoenix, AZ",
  min_price: 1000000,
  max_price: 10000000,
  strategy: "Value-add",
  enabled: true,
  cadence_hours: 12,
  query: "Phoenix retail",
  next_run_at: "2026-10-14T00:17:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  last_run_at: null,
  last_status: null,
  last_job_id: null,
  last_error: null,
};
async function send(text: string) {
  await screen.findByText("Workspace connected");
  fireEvent.change(screen.getByLabelText("Message RetailScope"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
}
function mockChat(result: ChatResult) {
  vi.spyOn(streams, "streamResearch").mockResolvedValue({
    id: "chat-job", status: "completed", leads: [],
    conversation_id: result.conversation_id,
    analysis: { answer: result.answer, sources: result.sources },
  });
  return vi.spyOn(api, "startChat").mockResolvedValue({ id: "chat-job", status: "queued" });
}

describe("chat-first workspace", () => {
  it("keeps agents collapsed and starts a fresh prompted chat from a selected agent", async () => {
    window.history.replaceState({}, "", "/?conversation=old");
    vi.spyOn(api, "conversation").mockResolvedValue({ id: "old", messages: [{ role: "assistant", content: "Old conversation" }] });
    vi.spyOn(api, "agents").mockResolvedValue({ agents: [agent] });
    const research = vi.spyOn(api, "research").mockResolvedValue({ id: "new-job", status: "queued" });
    vi.spyOn(streams, "streamResearch").mockResolvedValue({ id: "new-job", conversation_id: "fresh", status: "completed", leads: [], analysis: { answer: "Fresh result", sources: [] } });
    render(<App />);
    await screen.findByText("Old conversation");
    const agentsButton = screen.getByRole("button", { name: "Agents" });
    expect(agentsButton).toHaveAttribute("aria-expanded", "false");
    expect(api.agents).not.toHaveBeenCalled();
    fireEvent.click(agentsButton);
    expect(screen.getByText("Old conversation")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: /Phoenix watch/ }));
    await screen.findByRole("heading", { name: "Run history" });
    fireEvent.click(screen.getByRole("button", { name: "New chat from agent" }));
    await screen.findByText("Fresh result");
    expect(research).toHaveBeenCalledExactlyOnceWith(agent.query, expect.any(AbortSignal), undefined);
    expect(screen.queryByText("Old conversation")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?conversation=fresh");
  });
  it.each(["/", "/?conversation=restored"])("resumes an existing chat after reload at %s without resubmitting or duplicating the user turn", async (url) => {
    window.history.replaceState({}, "", url);
    vi.spyOn(api, "runs").mockResolvedValue({ runs: [{ id: "pending-job", conversation_id: "restored", kind: "chat", status: "running", leads: [] }] });
    vi.spyOn(api, "conversation").mockResolvedValue({ id: "restored", messages: [{ role: "user", content: "Saved question" }] });
    const start = vi.spyOn(api, "startChat");
    const research = vi.spyOn(api, "research");
    let finish!: (result: ResearchResult) => void;
    const stream = vi.spyOn(streams, "streamResearch").mockImplementation((_id, _signal, update) => {
      update({ id: "pending-job", status: "running", stage: "validating", leads: [], activity: [{ id: "a", stage: "validating", message: "Checking saved evidence", created_at: "2026-10-13T20:00:00Z" }] });
      return new Promise((resolve) => { finish = resolve; });
    });
    render(<App />);
    await screen.findByText("Checking saved evidence");
    expect(screen.getAllByText("Saved question")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    expect(stream).toHaveBeenCalledWith("pending-job", expect.any(AbortSignal), expect.any(Function));
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    act(() => finish({ id: "pending-job", conversation_id: "restored", status: "completed", leads: [], analysis: { answer: "Recovered answer", sources: [] } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled());
    expect(screen.getByRole("region", { name: "Artifacts" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(screen.getByText("Recovered answer")).toBeVisible();
    expect(screen.getAllByText("Saved question")).toHaveLength(1);
    expect(window.location.search).toBe("?conversation=restored");
    expect(start).not.toHaveBeenCalled();
    expect(research).not.toHaveBeenCalled();
  });
  it("does not replace a selected unrelated conversation with an active run", async () => {
    window.history.replaceState({}, "", "/?conversation=selected");
    vi.spyOn(api, "runs").mockResolvedValue({ runs: [{ id: "pending-job", conversation_id: "other", kind: "chat", status: "running", leads: [] }] });
    vi.spyOn(api, "conversation").mockResolvedValue({ id: "selected", messages: [{ role: "assistant", content: "Selected chat" }] });
    const stream = vi.spyOn(streams, "streamResearch");
    render(<App />);
    await screen.findByText("Selected chat");
    expect(stream).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?conversation=selected");
  });
  it("blocks paid submission when active-run recovery is unavailable", async () => {
    vi.spyOn(api, "runs").mockRejectedValue(new Error("Temporarily offline"));
    const start = vi.spyOn(api, "startChat");
    render(<App />);
    await screen.findByText(/Could not restore active work/);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
  });
  it("preserves a near-limit draft when filters exceed the payload limit and allows retry without filters", async () => {
    const research = vi.spyOn(api, "research").mockResolvedValue({ id: "r1", status: "queued" });
    const chat = vi.spyOn(api, "chat");
    const schedule = vi.spyOn(api, "agentDraft");
    vi.spyOn(streams, "streamResearch").mockResolvedValue({ id: "r1", status: "completed", leads: [] });
    const draft = "Find retail properties. " + "x".repeat(1976);
    expect(draft).toHaveLength(2000);
    render(<App />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: Off" }));
    await send(draft);
    expect(await screen.findByRole("alert")).toHaveTextContent("Shorten your message or turn off search filters");
    expect(screen.getByLabelText("Message RetailScope")).toHaveValue(draft);
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Stop waiting" })).toBeNull();
    expect(screen.queryByRole("article")).toBeNull();
    expect(research).not.toHaveBeenCalled();
    expect(chat).not.toHaveBeenCalled();
    expect(schedule).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: On" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(research).toHaveBeenCalledWith(draft, expect.any(AbortSignal), undefined));
    await waitFor(() => expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a single Chats control above history and keeps research filters off by default", async () => {
    render(<App />);
    await screen.findByText("Workspace connected");
    const sidebar = screen.getByLabelText("Workspace sidebar");
    expect(within(sidebar).getAllByText("Chats")).toHaveLength(1);
    expect(within(sidebar).queryByText("Conversations")).toBeNull();
    expect(screen.getByRole("button", { name: "Chats" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText("Past conversations")).not.toBeVisible();
    expect(within(screen.getByRole("navigation", { name: "Main workspace" })).getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: "Search filters: Off" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(screen.getByRole("button", { name: "Chats" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Past conversations")).toBeVisible();
    expect(screen.getByRole("region", { name: "Research chat" })).toBeVisible();
  });

  it.each([false, true])("applies preferences to fresh research only when enabled=%s", async (enabled) => {
    const research = vi.spyOn(api, "research").mockResolvedValue({ id: "r1", status: "queued" });
    vi.spyOn(streams, "streamResearch").mockResolvedValue({ id: "r1", status: "completed", leads: [] });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Search preferences" }));
    fireEvent.change(screen.getByLabelText("US city"), { target: { value: "Austin, TX" } });
    fireEvent.change(screen.getByLabelText("Min budget ($M)"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "Income" } });
    fireEvent.click(screen.getByRole("button", { name: "Close preferences" }));
    if (enabled) {
      fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: Off" }));
      expect(screen.getByText("Research only: Austin, TX · $2M to $10M · Income")).toBeVisible();
    }
    await send("Find retail properties");
    await waitFor(() => expect(research).toHaveBeenCalledOnce());
    const query = research.mock.calls[0][0];
    if (enabled) {
      expect(query).toContain("Austin, TX");
      expect(query).toContain("$2 million");
      expect(query).toContain("income opportunities");
    } else expect(query).toBe("Find retail properties");
    await waitFor(() => expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled());
  });

  it.each(["hi", "What risks should I verify?"])("keeps %s in free chat even with filters enabled", async (message) => {
    const chat = mockChat({ conversation_id: "c1", answer: "Chat reply", sources: [] });
    const research = vi.spyOn(api, "research");
    const schedule = vi.spyOn(api, "agentDraft");
    render(<App />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: Off" }));
    await send(message);
    await screen.findByText("Chat reply");
    expect(chat).toHaveBeenCalledWith({ message }, expect.any(AbortSignal));
    expect(research).not.toHaveBeenCalled();
    expect(schedule).not.toHaveBeenCalled();
  });

  it("never applies research filters to a schedule draft", async () => {
    const schedule = vi.spyOn(api, "agentDraft").mockResolvedValue({ draft: {}, message: "Review schedule", conversation_id: "s1", missing_fields: ["city"], warnings: [] });
    render(<App />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: Off" }));
    await send("Monitor retail properties daily");
    await screen.findByText("Review schedule");
    expect(schedule).toHaveBeenCalledWith("Monitor retail properties daily", undefined, expect.any(AbortSignal));
  });

  it.each(["Find shopping centers", "Schedule a background agent", "Use research prompt"])("routes an edited %s suggestion naturally", async (suggestion) => {
    const chat = mockChat({ conversation_id: "c1", answer: "Hello there", sources: [] });
    const research = vi.spyOn(api, "research");
    const schedule = vi.spyOn(api, "agentDraft");
    render(<App />);
    if (suggestion === "Use research prompt") fireEvent.click(screen.getByRole("button", { name: "Search preferences" }));
    fireEvent.click(screen.getByRole("button", { name: suggestion }));
    expect(screen.getByLabelText("Message mode")).toHaveValue("auto");
    if (suggestion !== "Schedule a background agent") expect(screen.getByRole("checkbox", { name: "Search filters: On" })).toBeChecked();
    await send("hi");
    await screen.findByText("Hello there");
    expect(chat).toHaveBeenCalledWith({ message: "hi" }, expect.any(AbortSignal));
    expect(research).not.toHaveBeenCalled();
    expect(schedule).not.toHaveBeenCalled();
  });

  it.each([false, true])("keeps generated prompts synchronized without doubled or disabled filters (disabled=%s)", async (disabled) => {
    const research = vi.spyOn(api, "research").mockResolvedValue({ id: "r1", status: "queued" });
    vi.spyOn(streams, "streamResearch").mockResolvedValue({ id: "r1", status: "completed", leads: [] });
    render(<App />);
    await screen.findByText("Workspace connected");
    fireEvent.click(screen.getByRole("button", { name: "Find shopping centers" }));
    fireEvent.click(screen.getByRole("button", { name: "Search preferences" }));
    fireEvent.change(screen.getByLabelText("US city"), { target: { value: "Austin, TX" } });
    fireEvent.click(screen.getByRole("button", { name: "Close preferences" }));
    if (disabled) fireEvent.click(screen.getByRole("checkbox", { name: "Search filters: On" }));
    const prompt = (screen.getByLabelText("Message RetailScope") as HTMLTextAreaElement).value;
    await send(`${prompt}\nPrefer corner lots.`);
    await waitFor(() => expect(research).toHaveBeenCalledOnce());
    const query = research.mock.calls[0][0];
    expect(query).not.toContain("Phoenix");
    expect(query).not.toContain("Search filters:");
    if (disabled) {
      expect(query).toBe("Find retail properties.\nPrefer corner lots.");
      expect(screen.queryByText(/Research only:/)).toBeNull();
    } else expect(query.match(/Austin, TX/g)).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "New chat" })).toBeEnabled());
  });

  it("keeps late chat replies in their thread without leaving Artifacts", async () => {
    let finish!: (result: any) => void;
    vi.spyOn(api, "startChat").mockResolvedValue({ id: "chat-job", status: "queued" });
    vi.spyOn(streams, "streamResearch").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await send("Evaluate the evidence");
    await waitFor(() => expect(finish).toBeDefined());
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    act(() =>
      finish({ id: "chat-job", status: "completed", leads: [], conversation_id: "c1", analysis: { answer: "Late answer", sources: [] } }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "New chat" }),
      ).not.toBeDisabled(),
    );
    expect(screen.getByRole("region", { name: "Artifacts" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(screen.getByText("Late answer")).toBeVisible();
    expect(screen.getByText("Evaluate the evidence")).toBeVisible();
  });
  it("blocks sending when saved history fails instead of silently creating a new thread", async () => {
    window.history.replaceState({}, "", "/?conversation=broken");
    vi.spyOn(api, "conversation").mockRejectedValue(
      new Error("History unavailable"),
    );
    vi.spyOn(api, "researchResult").mockRejectedValue(
      new Error("Not a research run"),
    );
    const chat = vi.spyOn(api, "chat");
    render(<App />);
    await screen.findByText("History unavailable");
    expect(screen.getByLabelText("Message RetailScope")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Refresh conversation" }),
    ).toBeEnabled();
    expect(chat).not.toHaveBeenCalled();
  });
  it("requires an explicit supported interval for an unsupported schedule", async () => {
    vi.spyOn(api, "agentDraft").mockResolvedValue({
      draft: {
        name: "Watch",
        city: "Phoenix, AZ",
        min_price: 1000000,
        max_price: 10000000,
        strategy: "Value-add",
      },
      message: "Choose a schedule.",
      conversation_id: "schedule1",
      missing_fields: ["cadence_hours"],
      warnings: ["Weekly is unsupported."],
    });
    render(<App />);
    await send("Schedule a weekly search");
    await screen.findByText("Review background agent");
    expect(screen.getByLabelText("Schedule")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Create agent" })).toBeDisabled();
    expect(screen.getByText("Weekly is unsupported.")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Schedule"), {
      target: { value: "24" },
    });
    expect(screen.getByRole("button", { name: "Create agent" })).toBeEnabled();
  });
  it("starts in one chat with sidebar sections and no invented leads", async () => {
    render(<App />);
    await screen.findByText("Workspace connected");
    expect(screen.getByRole("region", { name: "Research chat" })).toBeVisible();
    for (const name of ["Chats", "Artifacts", "Agents"])
      expect(screen.getByRole("button", { name })).toBeVisible();
    expect(screen.queryByLabelText("Lead discovery")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Inbox results")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
  it("renders markdown headings, lists and tables in the main chat", async () => {
    mockChat({
      conversation_id: "c1",
      answer:
        "## Evidence\n\n**Source reported**\n\n- Verify leases\n\n| Metric | Value |\n|---|---|\n| Area | Not provided |",
      sources: [],
    });
    render(<App />);
    await send("What evidence should I verify?");
    await screen.findByRole("heading", { name: "Evidence" });
    expect(screen.getByRole("table")).toHaveTextContent("Not provided");
    expect(screen.getByText("Source reported").tagName).toBe("STRONG");
  });
  it("preserves the response and draft across sidebar navigation", async () => {
    mockChat({
      conversation_id: "c1",
      answer: "Saved answer",
      sources: [],
    });
    render(<App />);
    await send("Evaluate the evidence");
    await screen.findByText("Saved answer");
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "Follow up" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    await screen.findByText("No saved artifacts yet.");
    fireEvent.click(screen.getByRole("button", { name: "Agents" }));
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(screen.getByText("Saved answer")).toBeVisible();
    expect(screen.getByLabelText("Message RetailScope")).toHaveValue(
      "Follow up",
    );
  });
  it("restores saved conversations and keeps their evidence context in follow-ups", async () => {
    vi.spyOn(api, "conversations").mockResolvedValue({
      conversations: [{ id: "saved", preview: "Prior research" }],
    });
    const read = vi.spyOn(api, "conversation").mockResolvedValue({
      id: "saved",
      messages: [{ role: "assistant", content: "Previous analysis" }],
    });
    const chat = mockChat({
      conversation_id: "saved",
      answer: "Continued analysis",
      sources: [],
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Prior research" }),
    );
    await screen.findByText("Previous analysis");
    await send("Continue");
    await screen.findByText("Continued analysis");
    expect(chat).toHaveBeenCalledWith(
      { message: "Continue", conversation_id: "saved" },
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    fireEvent.click(screen.getByRole("button", { name: "Prior research" }));
    expect(read).toHaveBeenCalledOnce();
    expect(window.location.search).toContain("conversation=saved");
  });
  it("does not show another thread while a conversation loads", async () => {
    vi.spyOn(api, "conversations").mockResolvedValue({
      conversations: [
        { id: "one", preview: "First" },
        { id: "two", preview: "Second" },
      ],
    });
    vi.spyOn(api, "conversation")
      .mockResolvedValueOnce({
        id: "one",
        messages: [{ role: "assistant", content: "First answer" }],
      })
      .mockImplementationOnce(() => new Promise(() => {}));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    fireEvent.click(await screen.findByRole("button", { name: "First" }));
    await screen.findByText("First answer");
    fireEvent.click(screen.getByRole("button", { name: "Second" }));
    await screen.findByText("Loading conversation");
    expect(screen.queryByText("First answer")).not.toBeInTheDocument();
  });
  it("streams early sources, then links the same durable artifact", async () => {
    vi.spyOn(api, "research").mockResolvedValue({
      id: "research1",
      status: "queued",
    });
    let update!: (result: ResearchResult) => void,
      finish!: (result: ResearchResult) => void;
    vi.spyOn(streams, "streamResearch").mockImplementation(
      (_id, _signal, onUpdate) => {
        update = onUpdate;
        return new Promise((resolve) => {
          finish = resolve;
        });
      },
    );
    render(<App />);
    await send("Find shopping centers in Phoenix");
    await waitFor(() => expect(update).toBeDefined());
    const leads = [
      { id: "l1", title: "Early listing", url: "https://example.org/listing" },
    ];
    act(() =>
      update({ id: "research1", status: "running", stage: "analyzing", leads }),
    );
    expect(screen.getByText("Analyzing retrieved evidence")).toBeVisible();
    expect(screen.getByText(/1 sources retrieved/)).toBeVisible();
    fireEvent.click(screen.getByText("1 retrieved sources"));
    expect(screen.getByRole("link", { name: "Early listing" })).toBeVisible();
    act(() =>
      finish({
        id: "research1",
        status: "completed",
        leads,
        analysis: { answer: "## Completed research", sources: [] },
      }),
    );
    await screen.findByRole("heading", { name: "Completed research" });
    vi.spyOn(api, "artifact").mockResolvedValue({
      schema_version: 1,
      id: "research1",
      title: "Phoenix report",
      created_at: new Date().toISOString(),
      status: "completed",
      query: "Phoenix",
      report: { answer: "Completed research", sources: [] },
      leads,
    });
    fireEvent.click(screen.getByRole("button", { name: /Research document/ }));
    await screen.findByRole("heading", { name: "Phoenix report" });
    expect(api.artifact).toHaveBeenCalledWith(
      "research1",
      expect.any(AbortSignal),
    );
  });
  it("streams chat activity before its verified answer and adopts the final conversation exactly once", async () => {
    const start = vi.spyOn(api, "startChat").mockResolvedValue({ id: "chat-job", status: "queued" });
    const legacy = vi.spyOn(api, "chat");
    let update!: (result: ResearchResult) => void;
    let finish!: (result: ResearchResult) => void;
    vi.spyOn(streams, "streamResearch").mockImplementation((_id, _signal, onUpdate) => {
      update = onUpdate;
      return new Promise((resolve) => { finish = resolve; });
    });
    render(<App />);
    await send("Explain the evidence");
    await waitFor(() => expect(update).toBeDefined());
    const first = { id: "1", stage: "analyzing", message: "Reviewing the saved evidence.", created_at: "2026-10-13T20:00:00Z" };
    const second = { id: "2", stage: "validating", message: "Checking the source citations.", created_at: "2026-10-13T20:00:01Z" };
    act(() => update({ id: "chat-job", status: "running", stage: "validating", leads: [], activity: [second, first, first] }));
    const log = screen.getByRole("log", { name: "Live agent activity" });
    expect(within(log).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining(first.message), expect.stringContaining(second.message),
    ]);
    expect(screen.queryByText("Verified reply")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(screen.getByText(second.message)).toBeVisible();
    act(() => finish({ id: "chat-job", conversation_id: "saved-chat", status: "completed", leads: [], analysis: { answer: "Verified reply", sources: [] } }));
    await screen.findByText("Verified reply");
    expect(screen.getAllByText("Explain the evidence")).toHaveLength(1);
    expect(window.location.search).toBe("?conversation=saved-chat");
    expect(screen.queryByRole("button", { name: /Research document/ })).toBeNull();
    expect(start).toHaveBeenCalledOnce();
    expect(legacy).not.toHaveBeenCalled();
  });
  it("keeps follow-up searches in their existing chat without losing or duplicating turns", async () => {
    window.history.replaceState({}, "", "/?conversation=saved");
    vi.spyOn(api, "conversation").mockResolvedValue({ id: "saved", messages: [
      { role: "user", content: "Find shopping centers in Phoenix" },
      { role: "assistant", content: "Prior analysis" },
    ] });
    const research = vi.spyOn(api, "research").mockResolvedValue({ id: "followup-job", status: "queued" });
    vi.spyOn(streams, "streamResearch").mockResolvedValue({ id: "followup-job", conversation_id: "saved", status: "completed", leads: [], analysis: { answer: "New verified findings", sources: [] } });
    render(<App />);
    await screen.findByText("Prior analysis");
    await send("Can you search for better resources?");
    await screen.findByText("New verified findings");
    expect(research).toHaveBeenCalledWith("Can you search for better resources?", expect.any(AbortSignal), "saved");
    expect(screen.getByText("Prior analysis")).toBeVisible();
    expect(screen.getAllByText("Can you search for better resources?")).toHaveLength(1);
    expect(window.location.search).toBe("?conversation=saved");
  });
  it("keeps partial research separate from an old answer and saves its artifact link", async () => {
    window.history.replaceState({}, "", "/?conversation=old");
    vi.spyOn(api, "conversation").mockResolvedValue({
      id: "old",
      messages: [{ role: "assistant", content: "Old answer" }],
    });
    vi.spyOn(api, "research").mockResolvedValue({
      id: "fresh",
      status: "queued",
    });
    vi.spyOn(streams, "streamResearch").mockResolvedValue({
      id: "fresh",
      status: "partial",
      leads: [],
      error: "Evidence check needs attention",
    });
    render(<App />);
    await screen.findByText("Old answer");
    await send("Find retail centers in Phoenix");
    await screen.findByText("Evidence check needs attention");
    expect(screen.getByText("Old answer")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Research document/ }),
    ).toBeVisible();
  });
  it("prepares an agent in chat but does not enroll until explicitly confirmed", async () => {
    const draft = vi.spyOn(api, "agentDraft").mockResolvedValue({
      draft: {
        name: "Phoenix watch",
        city: "Phoenix, AZ",
        min_price: 1000000,
        max_price: 10000000,
        strategy: "Value-add",
        cadence_hours: 12,
      },
      message: "Review this draft.",
      conversation_id: "schedule1",
      missing_fields: [],
      warnings: [],
    });
    const create = vi.spyOn(api, "createAgent").mockResolvedValue(agent);
    render(<App />);
    await send("Monitor centers in Phoenix every 12 hours");
    await screen.findByRole("heading", { name: "Review background agent" });
    expect(draft).toHaveBeenCalledOnce();
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Schedule")).toHaveValue("12");
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          cadence_hours: 12,
          city: "Phoenix, AZ",
          conversation_id: "schedule1",
        }),
      ),
    );
    await screen.findByText(/is scheduled every/);
    expect(
      screen.queryByRole("heading", { name: "Review background agent" }),
    ).not.toBeInTheDocument();
  });
  it("discards a schedule draft without enrollment", async () => {
    vi.spyOn(api, "agentDraft").mockResolvedValue({
      draft: {},
      message: "Add missing fields.",
      conversation_id: "schedule1",
      missing_fields: ["city"],
      warnings: [],
    });
    const create = vi.spyOn(api, "createAgent");
    render(<App />);
    await send("Create a background agent");
    await screen.findByText("Review background agent");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(create).not.toHaveBeenCalled();
  });
  it("retries bootstrap without starting provider work", async () => {
    vi.spyOn(api, "status")
      .mockRejectedValueOnce(new Error("API unavailable"))
      .mockResolvedValueOnce({
        providers: { snowflake: true, nimble: true },
        model: "test",
      });
    const research = vi.spyOn(api, "research");
    render(<App />);
    await screen.findByText("API unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Workspace connected");
    expect(research).not.toHaveBeenCalled();
  });
  it("does not fetch history or artifacts before the browser session is established", async () => {
    let finish!: (value: any) => void;
    vi.spyOn(api, "status").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    expect(api.conversations).not.toHaveBeenCalled();
    expect(api.artifacts).not.toHaveBeenCalled();
    act(() =>
      finish({ providers: { snowflake: true, nimble: true }, model: "test" }),
    );
    await waitFor(() => expect(api.conversations).toHaveBeenCalledOnce());
  });
  it("builds a research prompt inside the composer without another search screen", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Search preferences" }));
    fireEvent.change(screen.getByLabelText("US city"), {
      target: { value: "Austin, TX" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Use research prompt/ }),
    );
    expect(
      (screen.getByLabelText("Message RetailScope") as HTMLTextAreaElement)
        .value,
    ).toContain("Austin, TX");
    expect(screen.getByLabelText("Message mode")).toHaveValue("auto");
    expect(screen.getByRole("checkbox", { name: "Search filters: On" })).toBeChecked();
  });
  it("offers a new empty conversation without deleting previous history", async () => {
    mockChat({
      conversation_id: "c1",
      answer: "Retained answer",
      sources: [],
    });
    render(<App />);
    await send("Hello");
    await screen.findByText("Retained answer");
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Retained answer")).not.toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(screen.getByText("What can I help with?")).toBeVisible();
  });
  it("surfaces MCP token errors without showing unusable installation commands", async () => {
    vi.spyOn(api, "mcpToken").mockRejectedValue(
      new Error("Workspace MCP access is not configured."),
    );
    render(<App />);
    await screen.findByText("Workspace connected");
    fireEvent.click(screen.getByRole("link", { name: "MCP" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Generate access token" }),
    );
    await screen.findByText("Workspace MCP access is not configured.");
    expect(screen.queryByRole("tab", { name: "Codex" })).toBeNull();
  });
});

it("routes explicit research and scheduling requests and respects a manual mode", () => {
  expect(messageIntent("Find strip centers in Phoenix", "auto")).toBe(
    "research",
  );
  expect(messageIntent("Schedule a search every 12 hours", "auto")).toBe(
    "schedule",
  );
  expect(messageIntent("Which risks need more research?", "auto")).toBe("ask");
  expect(messageIntent("Explain what background agents do", "ask")).toBe("ask");
});
it.each(["Can you search for better resources?", "Search again", "Please investigate further", "Find additional evidence"])("recognizes investigative follow-ups: %s", (message) => {
  expect(messageIntent(message, "auto")).toBe("research");
  expect(messageIntent(message, "ask")).toBe("ask");
});
it.each([
  "how do background agents work?",
  "what should I watch out for?",
  "watch a movie",
  "Can you watch a movie?",
  "Explain daily property monitoring",
  "hi",
])("keeps conversational intent in Chat: %s", (message) => {
  expect(messageIntent(message, "auto")).toBe("ask");
});
it.each([
  "Please monitor retail properties daily",
  "Can you create a background agent?",
  "Schedule a weekly search",
  "Watch shopping centers in Phoenix every 12 hours",
])("recognizes explicit schedule intent: %s", (message) => {
  expect(messageIntent(message, "auto")).toBe("schedule");
});
it("renders safe markdown without raw HTML or remote tracking images", () => {
  const { container } = render(
    <Markdown>
      {
        "[Unsafe](javascript:alert(1)) [Safe](https://example.org) <script>alert(1)</script> ![Remote](https://example.org/tracker.png)"
      }
    </Markdown>,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(within(container).queryByRole("link", { name: "Unsafe" })).toBeNull();
  expect(screen.getByRole("link", { name: "Safe" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
});
it("preserves genuine zero metrics without converting missing values to zero", () => {
  const lead: Lead = {
    id: "l1",
    title: "Source",
    url: "javascript:alert(1)",
    facts: {
      annual_noi: {
        value: null,
        unit: "USD",
        status: "unknown",
        quote: null,
        source_url: null,
      },
      occupancy_pct: {
        value: 0,
        unit: "%",
        status: "source_reported",
        quote: "Vacant",
        source_url: "https://example.org/evidence",
      },
    },
  };
  const { container } = render(<LeadDetails lead={lead} />);
  expect(screen.getAllByText("Not provided")).toHaveLength(7);
  expect(screen.getByLabelText("Source-reported occupancy")).toHaveAttribute(
    "value",
    "0",
  );
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
});
