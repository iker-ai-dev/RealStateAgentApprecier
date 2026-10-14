import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type Agent, type AgentRun } from "./api";
import { AgentsWorkspace } from "./AgentsWorkspace";
import { streamResearch } from "./researchStream";

vi.mock("./api", async (original) => {
  const module = await original<typeof import("./api")>();
  return { ...module, api: { ...module.api, agentRuns: vi.fn() } };
});
vi.mock("./researchStream", async (original) => ({
  ...(await original<typeof import("./researchStream")>()),
  streamResearch: vi.fn(),
}));

const agent: Agent = {
  id: "agent1", name: "Phoenix watch", city: "Phoenix, AZ",
  min_price: 1000000, max_price: 10000000, strategy: "Value-add",
  enabled: true, cadence_hours: 12, query: "Phoenix retail",
  next_run_at: null, expires_at: "2099-01-01", last_run_at: null,
  last_status: "completed", last_job_id: "artifact1", last_error: null,
};
const otherAgent = { ...agent, id: "agent2", name: "San Diego watch", query: "San Diego retail" };
const props = {
  ready: true, active: true, onOpenReport: vi.fn(), onAgentsChanged: vi.fn(),
  onSelectAgent: vi.fn(), onStartChat: vi.fn(),
};
function run(id: string, status: AgentRun["status"], day = 1): AgentRun {
  return { id, status, created_at: `2026-10-${String(day).padStart(2, "0")}T12:00:00Z`,
    query: "Phoenix retail", lead_count: 2, has_report: status === "completed", agent_id: agent.id };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(api, "agents").mockResolvedValue({ agents: [agent, otherAgent] });
  vi.mocked(api.agentRuns).mockResolvedValue({ runs: [] });
  vi.spyOn(api, "createAgent").mockResolvedValue(agent);
  vi.spyOn(api, "runAgent").mockResolvedValue({ id: "job1", status: "queued" });
  vi.spyOn(api, "updateAgent").mockResolvedValue({ ...agent, enabled: false });
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it.each([6, 12, 24] as const)("creates at %i hours and selects the new agent without running research", async (cadence) => {
  render(<AgentsWorkspace {...props} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm and save agent" })).toBeEnabled());
  expect(screen.queryByText(agent.name)).toBeNull();
  expect(screen.queryByText(otherAgent.name)).toBeNull();
  expect(api.agentRuns).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "My watch" } });
  fireEvent.change(screen.getByLabelText("Run cadence"), { target: { value: String(cadence) } });
  expect(api.createAgent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm and save agent" }));
  await waitFor(() => expect(props.onSelectAgent).toHaveBeenCalledWith(agent.id));
  expect(api.createAgent).toHaveBeenCalledWith(
    expect.objectContaining({ name: "My watch", cadence_hours: cadence }), expect.any(AbortSignal));
  expect(props.onAgentsChanged).toHaveBeenCalled();
  expect(api.runAgent).not.toHaveBeenCalled();
});

it("shows only the selected agent, its configuration, and newest-first history", async () => {
  vi.mocked(api.agentRuns).mockResolvedValue({ runs: [run("old", "completed", 1), run("new", "completed", 3)] });
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByRole("heading", { name: agent.name });
  const history = await screen.findByRole("region", { name: "Run history" });
  await waitFor(() => expect(within(history).getAllByRole("button")).toHaveLength(2));
  expect(screen.queryByText(otherAgent.name)).toBeNull();
  expect(screen.queryByLabelText("Agent name")).toBeNull();
  expect(screen.getByText("Active · Every 12 hours")).toBeInTheDocument();
  expect(screen.getByText("Last run: Not yet")).toBeInTheDocument();
  const prompt = screen.getByText("Research prompt").closest("details");
  expect(prompt).not.toHaveAttribute("open");
  expect(prompt).toHaveTextContent(agent.query);
  const buttons = within(history).getAllByRole("button");
  fireEvent.click(buttons[0]);
  expect(props.onOpenReport).toHaveBeenLastCalledWith("new");
  fireEvent.click(buttons[1]);
  expect(props.onOpenReport).toHaveBeenLastCalledWith("old");
});

it("keeps all states visible but only opens reports for completed and partial runs", async () => {
  const states = ["queued", "running", "failed", "partial", "completed"] as const;
  vi.mocked(api.agentRuns).mockResolvedValue({ runs: states.map((status, index) => ({
    ...run(status, status, index + 1),
    ...(status === "failed" || status === "partial" ? { error: status + " evidence error" } : {}),
  })) });
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByText("failed evidence error");
  expect(screen.getByText("partial evidence error")).toBeInTheDocument();
  expect(screen.getByText("Getting started")).toBeInTheDocument();
  expect(screen.getByText("Researching listings")).toBeInTheDocument();
  const history = screen.getByRole("region", { name: "Run history" });
  const buttons = within(history).getAllByRole("button");
  expect(buttons).toHaveLength(5);
  buttons.forEach((button) => fireEvent.click(button));
  expect(props.onOpenReport.mock.calls.map(([id]) => id)).toEqual(["completed", "partial"]);
  expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
});

it.each(["queued", "running", "failed"] as const)("expands %s run details without artifact navigation", async (status) => {
  vi.mocked(api.agentRuns).mockResolvedValue({ runs: [{
    ...run("run-detail-id", status), query: "Investigate downtown retail",
    error: status === "failed" ? "Provider unavailable" : undefined,
  }] });
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  const button = await screen.findByRole("button", { name: /View run/ });
  expect(button).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(button);
  const details = screen.getByRole("region", { name: "Run details" });
  expect(within(details).getByText("Investigate downtown retail")).toBeInTheDocument();
  expect(within(details).getByText("run-detail-id")).toBeInTheDocument();
  expect(within(details).getByText("Status")).toBeInTheDocument();
  if (status === "failed") expect(within(details).getByText("Provider unavailable")).toBeInTheDocument();
  expect(props.onOpenReport).not.toHaveBeenCalled();
  expect(button).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(button);
  expect(screen.queryByRole("region", { name: "Run details" })).toBeNull();
});

it("starts a fresh chat with the agent query without launching research here", async () => {
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  fireEvent.click(await screen.findByRole("button", { name: "New chat from agent" }));
  expect(props.onStartChat).toHaveBeenCalledWith(agent.query);
  expect(api.runAgent).not.toHaveBeenCalled();
});

it("disables starting a fresh chat while the parent chat is busy", async () => {
  render(<AgentsWorkspace {...props} selectedId={agent.id} chatBusy />);
  const button = await screen.findByRole("button", { name: "New chat from agent" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(props.onStartChat).not.toHaveBeenCalled();
});

it("refreshes history after the streamed run completes", async () => {
  vi.mocked(streamResearch).mockImplementation(async (_id, _signal, update) => {
    update({ id: "job1", status: "running", stage: "searching", leads: [] });
    vi.mocked(api.agentRuns).mockResolvedValue({ runs: [run("job1", "completed")] });
    return { id: "job1", status: "completed", leads: [] };
  });
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByText("No runs yet.");
  fireEvent.click(screen.getByRole("button", { name: "Run now" }));
  fireEvent.click(await screen.findByRole("button", { name: /Open report/ }));
  expect(props.onOpenReport).toHaveBeenCalledWith("job1");
  expect(api.runAgent).toHaveBeenCalledTimes(1);
  expect(props.onAgentsChanged).toHaveBeenCalledTimes(2);
});

it("toggles the selected agent and notifies the parent", async () => {
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  fireEvent.click(await screen.findByRole("button", { name: "Pause Phoenix watch" }));
  await waitFor(() => expect(props.onAgentsChanged).toHaveBeenCalled());
  expect(api.updateAgent).toHaveBeenCalledWith(agent.id, false, expect.any(AbortSignal));
});

it("streams one manual run and does not duplicate it on refresh or repeated clicks", async () => {
  vi.mocked(streamResearch).mockImplementation(() => new Promise(() => {}));
  const { rerender, unmount } = render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByText("No runs yet.");
  const button = screen.getByRole("button", { name: "Run now" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(streamResearch).toHaveBeenCalledTimes(1));
  expect(api.runAgent).toHaveBeenCalledTimes(1);
  expect(props.onAgentsChanged).toHaveBeenCalled();
  rerender(<AgentsWorkspace {...props} selectedId={agent.id} refreshKey={1} />);
  await waitFor(() => expect(api.agentRuns).toHaveBeenCalledTimes(3));
  expect(api.runAgent).toHaveBeenCalledTimes(1);
  const signal = vi.mocked(streamResearch).mock.calls[0][1];
  unmount();
  expect(signal.aborted).toBe(true);
});

it("aborts and ignores stale history when selection changes", async () => {
  let resolveOld!: (value: { runs: AgentRun[] }) => void;
  vi.mocked(api.agentRuns).mockImplementation((id) => id === agent.id
    ? new Promise((resolve) => { resolveOld = resolve; })
    : Promise.resolve({ runs: [{ ...run("new-agent-run", "failed"), agent_id: otherAgent.id, error: "Current failure" }] }));
  const { rerender } = render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await waitFor(() => expect(api.agentRuns).toHaveBeenCalledTimes(1));
  const signal = vi.mocked(api.agentRuns).mock.calls[0][1];
  rerender(<AgentsWorkspace {...props} selectedId={otherAgent.id} />);
  await screen.findByText("Current failure");
  expect(signal?.aborted).toBe(true);
  await act(async () => resolveOld({ runs: [{ ...run("stale", "failed"), error: "Stale failure" }] }));
  expect(screen.queryByText("Stale failure")).toBeNull();
  expect(screen.queryByText(agent.name)).toBeNull();
});

it("refreshes history with refreshKey and does not fetch while inactive", async () => {
  const { rerender } = render(<AgentsWorkspace {...props} selectedId={agent.id} active={false} />);
  expect(api.agents).not.toHaveBeenCalled();
  rerender(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByText("No runs yet.");
  vi.mocked(api.agentRuns).mockResolvedValue({ runs: [run("fresh", "completed")] });
  rerender(<AgentsWorkspace {...props} selectedId={agent.id} refreshKey={1} />);
  fireEvent.click(await screen.findByRole("button", { name: /Open report/ }));
  expect(props.onOpenReport).toHaveBeenCalledWith("fresh");
});

it("polls at 30 seconds only while active", async () => {
  vi.useFakeTimers();
  const { rerender } = render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await act(async () => {});
  expect(api.agentRuns).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTime(30000));
  expect(api.agentRuns).toHaveBeenCalledTimes(2);
  rerender(<AgentsWorkspace {...props} selectedId={agent.id} active={false} />);
  await act(async () => vi.advanceTimersByTime(60000));
  expect(api.agentRuns).toHaveBeenCalledTimes(2);
});

it("shows a history failure and allows an explicit refresh", async () => {
  vi.mocked(api.agentRuns).mockRejectedValueOnce(new Error("History unavailable"));
  render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Refresh agent" }));
  await screen.findByText("No runs yet.");
});

it("shows paused and expired states and blocks expired actions", async () => {
  vi.mocked(api.agents).mockResolvedValue({ agents: [{ ...agent, enabled: false }] });
  const { rerender } = render(<AgentsWorkspace {...props} selectedId={agent.id} />);
  await screen.findByText("Paused · Every 12 hours");
  expect(screen.getByRole("button", { name: "Resume Phoenix watch" })).toBeEnabled();
  vi.mocked(api.agents).mockResolvedValue({ agents: [{ ...agent, expires_at: "2000-01-01" }] });
  rerender(<AgentsWorkspace {...props} selectedId={agent.id} refreshKey={1} />);
  await screen.findByText("Expired · Every 12 hours");
  expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Pause Phoenix watch" })).toBeDisabled();
});
