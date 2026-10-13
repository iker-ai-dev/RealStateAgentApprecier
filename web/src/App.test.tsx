import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import App from "./App";
import { api } from "./api";
import { Markdown } from "./Markdown";

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.spyOn(api, "status").mockResolvedValue({
    providers: { snowflake: true, nimble: false },
    model: "API model",
  });
  vi.spyOn(api, "leads").mockResolvedValue({ leads: [] });
  vi.spyOn(api, "conversations").mockResolvedValue({ conversations: [] });
});

describe("workspace", () => {
  it("starts empty without sample properties or invented answers", async () => {
    render(<App />);
    await screen.findByText("Your next opportunity starts here");
    expect(screen.getByText("No property selected")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { pressed: false })).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
  it("shows unavailable API errors and retries leads", async () => {
    vi.spyOn(api, "leads")
      .mockRejectedValueOnce(new Error("API offline"))
      .mockResolvedValueOnce({ leads: [] });
    render(<App />);
    await screen.findByText("API offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.queryByText("API offline")).not.toBeInTheDocument(),
    );
  });
  it("researches, selects a returned property, and sends its context", async () => {
    vi.spyOn(api, "research").mockResolvedValue({
      id: "job1",
      status: "pending",
    });
    vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "job1",
      status: "completed",
      leads: [
        {
          id: "lead1",
          title: "API property",
          url: "https://example.org/property",
          location: "Phoenix",
          summary: "Source assertion",
        },
      ],
    });
    const chat = vi.spyOn(api, "chat").mockResolvedValue({
      answer: "**Source-grounded answer**",
      sources: [
        { title: "Listing source", url: "https://example.org/property" },
      ],
      conversation_id: "conversation1",
    });
    render(<App />);
    await screen.findByText("Your next opportunity starts here");
    fireEvent.change(screen.getByLabelText("Research brief"), {
      target: { value: "Phoenix properties" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Find leads" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /API property/ }),
    );
    expect(
      screen.getByRole("link", { name: "Open original source" }),
    ).toHaveAttribute("href", "https://example.org/property");
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "What is missing?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Source-grounded answer");
    expect(chat).toHaveBeenCalledWith(
      { message: "What is missing?", lead_id: "lead1" },
      expect.any(AbortSignal),
    );
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "And the location?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() =>
      expect(chat).toHaveBeenLastCalledWith(
        {
          message: "And the location?",
          lead_id: "lead1",
          conversation_id: "conversation1",
        },
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Return to ad hoc chat" }),
    );
    expect(
      screen.queryByText("Source-grounded answer"),
    ).not.toBeInTheDocument();
  });
  it("shows partial research sources and warning without inventing an answer, and allows follow-up", async () => {
    const warning = "Sources were saved, but the AI analysis could not be verified. You can inspect the sources or ask a follow-up question.";
    vi.spyOn(api, "research").mockResolvedValue({ id: "job1", status: "queued" });
    const result = vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "job1",
      status: "partial",
      leads: [{ id: "lead1", title: "Saved source", url: "https://example.org/property" }],
      error: warning,
    });
    const chat = vi.spyOn(api, "chat").mockResolvedValue({
      answer: "Verified follow-up",
      sources: [],
      conversation_id: "follow-up1",
    });
    render(<App />);
    await screen.findByText("Your next opportunity starts here");
    fireEvent.change(screen.getByLabelText("Research brief"), {
      target: { value: "Phoenix properties" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Find leads" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    expect(screen.getByRole("status")).toHaveTextContent("Sources saved; analysis unavailable");
    expect(screen.getByRole("button", { name: "Find leads" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Refresh leads" })).toBeEnabled();
    expect(screen.getByLabelText("Research brief")).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("article")).toHaveLength(0);
    expect(chat).not.toHaveBeenCalled();
    expect(result).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: /Saved source/ }));
    expect(screen.getByRole("link", { name: "Open original source" })).toHaveAttribute("href", "https://example.org/property");
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "What should I verify?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Verified follow-up");
    expect(chat).toHaveBeenCalledWith(
      { message: "What should I verify?", lead_id: "lead1" },
      expect.any(AbortSignal),
    );
  });
  it("keeps ad hoc chat independent and surfaces failures", async () => {
    const chat = vi
      .spyOn(api, "chat")
      .mockRejectedValue(new Error("Chat unavailable"));
    render(<App />);
    await screen.findByText("Your next opportunity starts here");
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "Research a market" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Chat unavailable");
    expect(chat).toHaveBeenCalledWith(
      { message: "Research a market" },
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Research a market")).toBeInTheDocument();
  });
  it("shows completed research analysis with source attribution in chat", async () => {
    vi.spyOn(api, "research").mockResolvedValue({
      id: "job1",
      status: "queued",
    });
    vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "job1",
      status: "completed",
      leads: [],
      analysis: {
        answer: "Cortex acquisition report",
        sources: [
          { title: "Research evidence", url: "https://example.org/evidence" },
        ],
        model: "Cortex model",
        usage: { tokens: 20 },
      },
    });
    render(<App />);
    await screen.findByText("Your next opportunity starts here");
    fireEvent.change(screen.getByLabelText("Research brief"), {
      target: { value: "Analyze Phoenix retail" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Find leads" }));
    await screen.findByText("Cortex acquisition report");
    expect(screen.getByText("Cortex model")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Research evidence" }),
    ).toHaveAttribute("href", "https://example.org/evidence");
  });
  it("attaches raw calculated scenario assumptions to selected-property chat", async () => {
    vi.spyOn(api, "leads").mockResolvedValue({
      leads: [
        { id: "lead1", title: "API property", url: "https://example.org" },
      ],
    });
    vi.spyOn(api, "scenario").mockResolvedValue({
      irr: 0.1,
      equity_multiple: 2,
      initial_equity: 1000000,
      annual_cash_flows: [],
      assumptions: { purchase_price: 1000000 },
    });
    const chat = vi.spyOn(api, "chat").mockResolvedValue({
      answer: "Scenario interpretation",
      sources: [],
      conversation_id: "c1",
    });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: /API property/ }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Underwriting" }));
    for (const [label, value] of [
      [/Purchase price/, "1000000"],
      [/Annual NOI/, "100000"],
      [/Exit cap rate/, "6"],
    ] as const)
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Calculate scenario" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Ask about this model" }),
    );
    expect(screen.getByLabelText("Attach calculated scenario")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Scenario interpretation");
    expect(chat.mock.calls[0][0]).toMatchObject({
      lead_id: "lead1",
      scenario: {
        purchase_price: 1000000,
        annual_noi: 100000,
        exit_cap_rate: 0.06,
        hold_years: 7,
      },
    });
  });
  it("renders the MCP route and copies exact remote registration", async () => {
    vi.spyOn(api, "mcpToken").mockResolvedValue({
      token: "test-secret",
      expires_at: Math.floor(Date.now() / 1000) + 604800,
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<App />);
    fireEvent.click(screen.getByRole("link", { name: "MCP" }));
    expect(window.location.pathname).toBe("/landing");
    expect(screen.queryByRole("tab", { name: "Claude Code" })).toBeNull();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Generate access token" }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Generate access token" }),
    );
    await screen.findByRole("tab", { name: "Claude Code" });
    expect(screen.getByLabelText("Workspace token")).toHaveAttribute(
      "type",
      "password",
    );
    expect(document.body.textContent).not.toContain("test-secret");
    fireEvent.click(screen.getByRole("tab", { name: "Claude Code" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Copy Claude Code command" }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        `claude mcp add --transport http retailscope-ai "${window.location.origin}/mcp" --header "Authorization: Bearer \${RETAILSCOPE_MCP_TOKEN}"`,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Workspace" }));
    expect(window.location.pathname).toBe("/");
  });
  it("restores a selected persisted thread after reload via the URL", async () => {
    window.history.replaceState({}, "", "/?conversation=saved1");
    const detail = vi.spyOn(api, "conversation").mockResolvedValue({
      id: "saved1",
      messages: [
        { role: "user", content: "Saved question" },
        {
          role: "assistant",
          content: "Persisted API answer",
          sources: [
            {
              title: "Persisted evidence",
              url: "https://example.org/evidence",
            },
            { title: "Unsafe source", url: "javascript:alert(1)" },
          ],
          model: "Saved Cortex model",
          usage: { tokens: 25 },
        },
      ],
    });
    vi.spyOn(api, "conversations").mockResolvedValue({
      conversations: [
        { id: "saved1", updated_at: "2026-10-12", preview: "Saved question" },
      ],
    });
    const first = render(<App />);
    await screen.findByText("Persisted API answer");
    expect(screen.getByLabelText("Chat history")).toHaveValue("saved1");
    first.unmount();
    render(<App />);
    await screen.findByText("Persisted API answer");
    expect(detail).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("link", { name: "Persisted evidence" }),
    ).toHaveAttribute("href", "https://example.org/evidence");
    expect(
      screen.getByRole("link", { name: "Persisted evidence" }),
    ).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Saved Cortex model")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Unsafe source" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(window.location.search).toBe("");
    expect(screen.queryByText("Persisted API answer")).toBeNull();
  });
  it("selects saved history and retains its conversation id when continuing", async () => {
    vi.spyOn(api, "conversations").mockResolvedValue({
      conversations: [
        { id: "saved1", updated_at: 123, preview: "Prior research" },
      ],
    });
    vi.spyOn(api, "conversation").mockResolvedValue({
      id: "saved1",
      messages: [{ role: "assistant", content: "Previous analysis" }],
    });
    const chat = vi.spyOn(api, "chat").mockResolvedValue({
      answer: "Continued analysis",
      sources: [],
      conversation_id: "saved1",
    });
    render(<App />);
    await screen.findByRole("option", { name: "Prior research" });
    fireEvent.change(screen.getByLabelText("Chat history"), {
      target: { value: "saved1" },
    });
    await screen.findByText("Previous analysis");
    fireEvent.change(screen.getByLabelText("Message RetailScope"), {
      target: { value: "Continue" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Continued analysis");
    expect(chat).toHaveBeenCalledWith(
      { message: "Continue", conversation_id: "saved1" },
      expect.any(AbortSignal),
    );
  });
});

it("bootstraps the session before loading leads or history", async () => {
  let resolve!: (value: {
    providers: { snowflake: boolean; nimble: boolean };
    model: string;
  }) => void;
  vi.spyOn(api, "status").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const leads = vi.spyOn(api, "leads").mockResolvedValue({ leads: [] });
  const history = vi
    .spyOn(api, "conversations")
    .mockResolvedValue({ conversations: [] });
  render(<App />);
  expect(leads).not.toHaveBeenCalled();
  expect(history).not.toHaveBeenCalled();
  resolve({
    providers: { snowflake: false, nimble: false },
    model: "API model",
  });
  await waitFor(() => {
    expect(leads).toHaveBeenCalledOnce();
    expect(history).toHaveBeenCalledOnce();
  });
});

it("surfaces token configuration errors without exposing install commands", async () => {
  vi.spyOn(api, "mcpToken").mockRejectedValue(
    new Error("Workspace MCP access is not configured."),
  );
  render(<App />);
  await screen.findByText("Your next opportunity starts here");
  fireEvent.click(screen.getByRole("link", { name: "MCP" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Generate access token" }),
  );
  await screen.findByText("Workspace MCP access is not configured.");
  expect(screen.queryByRole("tab", { name: "Codex" })).toBeNull();
  expect(screen.queryByLabelText("Workspace token")).toBeNull();
});

it("renders Markdown with safe links and no raw HTML or image requests", () => {
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
  expect(within(container).getByRole("link", { name: "Safe" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
});
