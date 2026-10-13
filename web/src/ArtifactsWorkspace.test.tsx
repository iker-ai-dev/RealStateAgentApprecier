import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Artifact } from "./api";
import { ArtifactsWorkspace } from "./ArtifactsWorkspace";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    api: {
      ...original.api,
      artifacts: vi.fn(),
      artifact: vi.fn(),
      scenario: vi.fn(),
      chat: vi.fn(),
      research: vi.fn(),
      runAgent: vi.fn(),
      createAgent: vi.fn(),
    },
  };
});

const document: Artifact = {
  schema_version: 1,
  id: "report1",
  title: "Phoenix report",
  created_at: "2026-10-13T12:00:00Z",
  status: "completed",
  query: "Find Phoenix retail",
  report: {
    answer: "**Verified narrative**",
    sources: [
      { title: "Evidence", url: "https://example.org/evidence" },
      { title: "Unsafe evidence", url: "javascript:alert(1)" },
    ],
  },
  leads: [
    {
      id: "l1",
      title: "First listing",
      url: "https://example.org/first",
      facts: {
        occupancy_pct: {
          value: 65,
          unit: "%",
          status: "source_reported",
          quote: "65% occupied",
          source_url: "https://example.org/first",
        },
      },
    },
    { id: "l2", title: "Second listing", url: "https://example.org/second" },
  ],
};
const props = { ready: true, active: true, onOpenChat: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.artifacts).mockResolvedValue({ artifacts: [document] });
  vi.mocked(api.artifact).mockResolvedValue(document);
});

describe("ArtifactsWorkspace", () => {
  it("keeps financial scenario inputs separate for properties in the same source", async () => {
    vi.mocked(api.artifact).mockResolvedValue({
      ...document,
      leads: [{ ...document.leads[0], properties: [
        { id: "a", title: "University Ave", url: "https://example.org/first", summary: "University listing", facts: {} },
        { id: "b", title: "Kettner Blvd", url: "https://example.org/first", summary: "Kettner listing", facts: {} },
      ] }],
    });
    render(<ArtifactsWorkspace {...props} selectedId="report1" />);
    await screen.findByRole("combobox", { name: "Property from source" });
    fireEvent.click(screen.getByText("Financial scenario"));
    fireEvent.change(screen.getByLabelText(/Purchase price/), { target: { value: "6000000" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Property from source" }), { target: { value: "b" } });
    fireEvent.click(screen.getByText("Financial scenario"));
    expect(screen.getByLabelText(/Purchase price/)).toHaveValue(null);
    expect(screen.getByRole("heading", { name: "Kettner Blvd" })).toBeVisible();
  });

  it("rejects mismatched document IDs without replacing a cached report", async () => {
    const { rerender } = render(
      <ArtifactsWorkspace {...props} selectedId="report1" />,
    );
    await screen.findByText("Verified narrative");
    vi.mocked(api.artifact).mockResolvedValue({
      ...document,
      id: "wrong",
      report: { ...document.report!, answer: "Wrong report" },
    });
    rerender(
      <ArtifactsWorkspace {...props} selectedId="report1" refreshKey={1} />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "does not match the requested document",
    );
    expect(screen.getByText("Verified narrative")).toBeInTheDocument();
    expect(screen.queryByText("Wrong report")).toBeNull();
    rerender(
      <ArtifactsWorkspace {...props} selectedId="uncached" refreshKey={1} />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "does not match the requested document",
    );
    expect(screen.queryByText("Wrong report")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Download document JSON" }),
    ).toBeNull();
  });

  it("saves calculated scenarios per artifact and source without automatic AI calls", async () => {
    vi.mocked(api.scenario).mockResolvedValue({
      irr: 0.1,
      equity_multiple: 2,
      initial_equity: 1000000,
      annual_cash_flows: [],
      assumptions: { purchase_price: 1000000 },
    });
    const { rerender } = render(
      <ArtifactsWorkspace {...props} selectedId="report1" />,
    );
    const summary = await screen.findByText("Financial scenario");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(summary);
    expect(
      screen.getByRole("button", { name: "Calculate scenario" }),
    ).toBeVisible();
    expect(api.scenario).not.toHaveBeenCalled();
    for (const [label, value] of [
      [/Purchase price/, "1000000"],
      [/Annual NOI/, "100000"],
      [/Exit cap rate/, "6"],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Calculate scenario" }));
    await screen.findByRole("region", { name: "Calculated scenario" });
    expect(
      screen.getByText(
        "Calculated from user-supplied assumptions. Not verified property financials.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Second listing" }));
    fireEvent.click(screen.getByText("Financial scenario"));
    expect(screen.getByLabelText(/Purchase price/)).toHaveValue(null);
    expect(
      screen.queryByRole("region", { name: "Calculated scenario" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "First listing" }));
    fireEvent.click(screen.getByText("Financial scenario"));
    expect(screen.getByLabelText(/Purchase price/)).toHaveValue(1000000);
    expect(
      screen.getByRole("region", { name: "Calculated scenario" }),
    ).toBeInTheDocument();

    vi.mocked(api.artifact).mockResolvedValue({ ...document, id: "report2" });
    rerender(<ArtifactsWorkspace {...props} selectedId="report2" />);
    await waitFor(() =>
      expect(screen.getByLabelText("Artifact document")).toHaveAttribute(
        "aria-busy",
        "false",
      ),
    );
    fireEvent.click(screen.getByText("Financial scenario"));
    expect(screen.getByLabelText(/Purchase price/)).toHaveValue(null);
    vi.mocked(api.artifact).mockResolvedValue(document);
    rerender(<ArtifactsWorkspace {...props} selectedId="report1" />);
    await waitFor(() =>
      expect(screen.getByLabelText("Artifact document")).toHaveAttribute(
        "aria-busy",
        "false",
      ),
    );
    fireEvent.click(screen.getByText("Financial scenario"));
    expect(screen.getByLabelText(/Purchase price/)).toHaveValue(1000000);
    expect(
      screen.getByRole("region", { name: "Calculated scenario" }),
    ).toBeInTheDocument();
    expect(api.scenario).toHaveBeenCalledOnce();
    expect(api.chat).not.toHaveBeenCalled();
    expect(api.research).not.toHaveBeenCalled();
    expect(api.runAgent).not.toHaveBeenCalled();
    expect(api.createAgent).not.toHaveBeenCalled();
    expect(props.onOpenChat).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Ask about this model" }));
    expect(props.onOpenChat).toHaveBeenCalledWith("report1", {
      input: vi.mocked(api.scenario).mock.calls[0][0],
      result: await vi.mocked(api.scenario).mock.results[0].value,
    });
    expect(api.chat).not.toHaveBeenCalled();
  });

  it("opens durable documents, shares lead details, and routes the saved chat", async () => {
    const onSelect = vi.fn();
    const { container } = render(
      <ArtifactsWorkspace {...props} onSelect={onSelect} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Phoenix report/ }),
    );
    expect(onSelect).toHaveBeenCalledWith("report1");
    await screen.findByText("Verified narrative");
    expect(screen.getByLabelText("Source-reported occupancy")).toHaveAttribute(
      "value",
      "65",
    );
    expect(screen.getByRole("link", { name: "Evidence" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Second listing" }));
    expect(
      screen.getByRole("link", { name: "Open original source" }),
    ).toHaveAttribute("href", "https://example.org/second");
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(props.onOpenChat).toHaveBeenCalledWith("report1");
  });

  it("retains cached content while refreshing and after a refresh failure", async () => {
    const { rerender } = render(
      <ArtifactsWorkspace {...props} selectedId="report1" />,
    );
    await screen.findByText("Verified narrative");
    let reject!: (reason: Error) => void;
    vi.mocked(api.artifact).mockImplementationOnce(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    );
    rerender(
      <ArtifactsWorkspace {...props} selectedId="report1" refreshKey={1} />,
    );
    expect(screen.getByText("Verified narrative")).toBeInTheDocument();
    await act(async () => reject(new Error("Refresh offline")));
    expect(await screen.findByText("Refresh offline")).toBeInTheDocument();
    expect(screen.getByText("Verified narrative")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry document" }));
    await waitFor(() =>
      expect(screen.queryByText("Refresh offline")).toBeNull(),
    );
  });

  it("aborts stale selections and ignores late responses", async () => {
    let resolve!: (value: Artifact) => void;
    vi.mocked(api.artifact).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const { rerender } = render(
      <ArtifactsWorkspace {...props} selectedId="old" />,
    );
    const signal = vi.mocked(api.artifact).mock.calls[0][1];
    rerender(<ArtifactsWorkspace {...props} selectedId="report1" />);
    await screen.findByText("Verified narrative");
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      resolve({
        ...document,
        id: "old",
        report: { ...document.report!, answer: "Stale answer" },
      }),
    );
    expect(screen.queryByText("Stale answer")).toBeNull();
  });

  it("shows partial sources without inventing a narrative and allows follow-up", async () => {
    vi.mocked(api.artifact).mockResolvedValue({
      ...document,
      status: "partial",
      report: null,
      error: "Analysis failed",
    });
    render(<ArtifactsWorkspace {...props} selectedId="report1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Analysis failed",
    );
    expect(screen.getByText("Analysis unavailable.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "First listing" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(props.onOpenChat).toHaveBeenCalledWith("report1");
  });

  it("waits for readiness and activation, and renders an empty library", async () => {
    vi.mocked(api.artifacts).mockResolvedValue({ artifacts: [] });
    const { rerender } = render(
      <ArtifactsWorkspace {...props} ready={false} />,
    );
    expect(api.artifacts).not.toHaveBeenCalled();
    rerender(<ArtifactsWorkspace {...props} active={false} />);
    expect(api.artifacts).not.toHaveBeenCalled();
    rerender(<ArtifactsWorkspace {...props} />);
    expect(
      await screen.findByText("No saved artifacts yet."),
    ).toBeInTheDocument();
  });

  it("keeps document and source selection when hidden and reactivated", async () => {
    const { rerender } = render(
      <ArtifactsWorkspace {...props} selectedId="report1" />,
    );
    await screen.findByText("Verified narrative");
    fireEvent.click(screen.getByRole("button", { name: "Second listing" }));
    rerender(
      <ArtifactsWorkspace {...props} selectedId="report1" active={false} />,
    );
    expect(screen.getByText("Verified narrative")).not.toBeVisible();
    rerender(<ArtifactsWorkspace {...props} selectedId="report1" />);
    expect(screen.getByText("Verified narrative")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Second listing" }),
    ).toHaveAttribute("aria-pressed", "true");
    await waitFor(() =>
      expect(screen.getByLabelText("Artifact document")).toHaveAttribute(
        "aria-busy",
        "false",
      ),
    );
  });

  it("retries list failures and filters saved summaries", async () => {
    vi.mocked(api.artifacts).mockRejectedValueOnce(
      new Error("Library offline"),
    );
    render(<ArtifactsWorkspace {...props} />);
    await screen.findByText("Library offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry artifacts" }));
    await screen.findByRole("button", { name: /Phoenix report/ });
    fireEvent.change(screen.getByLabelText("Search artifacts"), {
      target: { value: "Boston" },
    });
    expect(screen.getByText("No matching artifacts.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search artifacts"), {
      target: { value: "retail" },
    });
    expect(
      screen.getByRole("button", { name: /Phoenix report/ }),
    ).toBeInTheDocument();
  });

  it("shows failed documents without fabricated analysis or sources", async () => {
    vi.mocked(api.artifact).mockResolvedValue({
      ...document,
      report: null,
      leads: [],
      status: "failed",
      error: "Research failed",
    });
    render(<ArtifactsWorkspace {...props} selectedId="report1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Research failed",
    );
    expect(screen.getByText("No saved sources.")).toBeInTheDocument();
    expect(screen.getByText("Analysis unavailable.")).toBeInTheDocument();
  });

  it("downloads the complete versioned document as JSON", async () => {
    const create = vi.fn().mockReturnValue("blob:test");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      render(<ArtifactsWorkspace {...props} selectedId="report1" />);
      await screen.findByText("Verified narrative");
      fireEvent.click(
        screen.getByRole("button", { name: "Download document JSON" }),
      );
      expect(create).toHaveBeenCalledWith(expect.any(Blob));
      const blob = create.mock.calls[0][0] as Blob;
      expect(blob.type).toBe("application/json");
      const reader = new FileReader();
      const text = new Promise((resolve) => {
        reader.onload = () => resolve(reader.result);
      });
      reader.readAsText(blob);
      expect(JSON.parse((await text) as string)).toEqual(document);
      expect(click).toHaveBeenCalledOnce();
      await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:test"), {
        timeout: 2000,
      });
    } finally {
      click.mockRestore();
      vi.unstubAllGlobals();
    }
  });
});
