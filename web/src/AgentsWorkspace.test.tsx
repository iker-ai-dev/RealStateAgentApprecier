import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type Agent } from "./api";
import { AgentsWorkspace } from "./AgentsWorkspace";

const agent: Agent = {
  id: "agent1",
  name: "Phoenix watch",
  city: "Phoenix, AZ",
  min_price: 1000000,
  max_price: 10000000,
  strategy: "Value-add",
  enabled: true,
  cadence_hours: 12,
  query: "Phoenix retail",
  next_run_at: null,
  expires_at: "2099-01-01",
  last_run_at: null,
  last_status: "completed",
  last_job_id: "artifact1",
  last_error: null,
};
const props = {
  ready: true,
  active: true,
  onOpenReport: vi.fn(),
  onResearchLead: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(api, "agents").mockResolvedValue({ agents: [] });
  vi.spyOn(api, "leads").mockResolvedValue({ leads: [] });
  vi.spyOn(api, "createAgent").mockResolvedValue(agent);
  vi.spyOn(api, "runAgent").mockResolvedValue({ id: "job1", status: "queued" });
});
afterEach(() => vi.restoreAllMocks());

it.each([6, 12, 24] as const)(
  "creates an agent at %i hours only after explicit confirmation",
  async (cadence) => {
    render(<AgentsWorkspace {...props} />);
    await screen.findByText("No background agents yet.");
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));
    fireEvent.change(screen.getByLabelText("Agent name"), {
      target: { value: "My watch" },
    });
    fireEvent.change(screen.getByLabelText("Run cadence"), {
      target: { value: String(cadence) },
    });
    expect(api.createAgent).not.toHaveBeenCalled();
    expect(api.runAgent).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm and save agent" }),
    );
    await waitFor(() =>
      expect(api.createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ name: "My watch", cadence_hours: cadence }),
        expect.any(AbortSignal),
      ),
    );
    expect(api.runAgent).not.toHaveBeenCalled();
    expect(api.leads).not.toHaveBeenCalled();
    expect(screen.queryByText("Inbox")).toBeNull();
  },
);

it("refreshes after a chat creates an agent and opens its artifact", async () => {
  const { rerender } = render(<AgentsWorkspace {...props} />);
  await screen.findByText("No background agents yet.");
  vi.mocked(api.agents).mockResolvedValue({ agents: [agent] });
  rerender(<AgentsWorkspace {...props} refreshKey={1} />);
  await screen.findByText("Phoenix watch");
  fireEvent.click(screen.getByRole("button", { name: "Open report" }));
  expect(props.onOpenReport).toHaveBeenCalledWith("artifact1");
  expect(api.leads).not.toHaveBeenCalled();
});
