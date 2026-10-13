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
import { api, type Agent, type Lead, type ResearchResult } from "./api";
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
  vi.spyOn(api, "agents").mockResolvedValue({ agents: [] });
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

describe("chat-first workspace", () => {
  it("keeps late chat replies in their thread without leaving Artifacts", async () => {
    let finish!: (result: any) => void;
    vi.spyOn(api, "chat").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<App />);
    await send("Evaluate the evidence");
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    act(() =>
      finish({ conversation_id: "c1", answer: "Late answer", sources: [] }),
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
    vi.spyOn(api, "chat").mockResolvedValue({
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
    vi.spyOn(api, "chat").mockResolvedValue({
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
    const chat = vi.spyOn(api, "chat").mockResolvedValue({
      conversation_id: "saved",
      answer: "Continued analysis",
      sources: [],
    });
    render(<App />);
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
    expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
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
    expect(screen.getByLabelText("Message mode")).toHaveValue("research");
  });
  it("offers a new empty conversation without deleting previous history", async () => {
    vi.spyOn(api, "chat").mockResolvedValue({
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
    expect(screen.getByText("What should we research?")).toBeVisible();
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
