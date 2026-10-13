import { afterEach, describe, expect, it, vi } from "vitest";
import { api, pollResearch, safeUrl } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("API contract", () => {
  it("preserves nullable facts and inbox metadata in leads and research results", async () => {
    const lead = {
      id: "l1",
      title: "Source",
      url: "https://example.org",
      property_type: "Retail",
      agent_id: "a1",
      job_id: "j1",
      read_at: null,
      facts: {
        asking_price: {
          value: 2000000,
          unit: "USD",
          quote: "$2 million",
          source_url: "https://example.org",
          status: "source_reported",
        },
        annual_noi: {
          value: null,
          unit: "USD/year",
          quote: null,
          source_url: null,
          status: "unknown",
        },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response(JSON.stringify({ leads: [lead] })))
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({ id: "j1", status: "partial", leads: [lead] }),
          ),
        ),
    );
    expect((await api.leads()).leads[0]).toEqual(lead);
    expect((await api.researchResult("j1")).leads[0]).toEqual(lead);
  });
  it("uses exact agent methods, encoded IDs, and read acknowledgement contracts", async () => {
    const agent = {
      id: "agent/a",
      name: "Watch",
      city: "Phoenix, AZ",
      min_price: 1000000,
      max_price: 5000000,
      strategy: "Income",
      enabled: true,
      cadence_hours: 6,
      query: "Research",
      next_run_at: "2026-10-13T00:17:00Z",
      expires_at: "2026-11-01T00:17:00Z",
      last_run_at: null,
      last_status: null,
      last_job_id: null,
      last_error: null,
    };
    const fetch = vi.fn();
    for (const result of [
      { agents: [agent] },
      agent,
      { ...agent, enabled: false },
      { id: "j1", status: "queued" },
      { ok: true },
    ]) {
      fetch.mockResolvedValueOnce(new Response(JSON.stringify(result)));
    }
    vi.stubGlobal("fetch", fetch);
    expect((await api.agents()).agents).toEqual([agent]);
    const input = {
      name: agent.name,
      city: agent.city,
      min_price: agent.min_price,
      max_price: agent.max_price,
      strategy: agent.strategy,
    };
    await api.createAgent(input);
    await api.updateAgent("agent/a", false);
    await api.runAgent("agent/a");
    await api.readLead("lead/a");
    expect(
      fetch.mock.calls.map(([url, options]) => [
        url,
        options.method,
        options.body && JSON.parse(options.body),
      ]),
    ).toEqual([
      ["/api/agents", "GET", undefined],
      ["/api/agents", "POST", input],
      ["/api/agents/agent%2Fa", "POST", { enabled: false }],
      ["/api/agents/agent%2Fa/run", "POST", {}],
      ["/api/leads/lead%2Fa/read", "POST", {}],
    ]);
  });
  it("preserves optional restored-message metadata and accepts older messages", async () => {
    const metadata = {
      sources: [{ title: "Evidence", url: "https://example.org" }],
      model: "Cortex",
      usage: { tokens: 25 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            id: "saved1",
            messages: [
              { role: "user", content: "Question" },
              { role: "assistant", content: "Answer", ...metadata },
            ],
          }),
        ),
      ),
    );
    const thread = await api.conversation("saved1");
    expect(thread.messages[0]).toEqual({ role: "user", content: "Question" });
    expect(thread.messages[1]).toEqual({
      role: "assistant",
      content: "Answer",
      ...metadata,
    });
  });
  it("sends only supplied chat context", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          answer: "Answer",
          sources: [],
          conversation_id: "c1",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    await api.chat({
      message: "Question",
      lead_id: "l1",
      conversation_id: "c0",
    });
    expect(fetch.mock.calls[0][0]).toBe("/api/chat");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      message: "Question",
      lead_id: "l1",
      conversation_id: "c0",
    });
  });
  it("rejects invalid payloads instead of manufacturing results", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ leads: [{ title: "Missing ID" }] })),
        ),
    );
    await expect(api.leads()).rejects.toThrow("expected format");
  });
  it("handles HTTP errors and non-JSON gateway responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 503 })),
    );
    await expect(api.status()).rejects.toThrow("503");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>Gateway</html>")),
    );
    await expect(api.status()).rejects.toThrow("unreadable");
  });
  it("encodes research IDs and sends the brief", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "job/a", status: "pending" })),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ id: "job/a", status: "completed", leads: [] }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await api.research("Phoenix centers");
    await api.researchResult("job/a");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      query: "Phoenix centers",
    });
    expect(fetch.mock.calls[1][0]).toBe("/api/research/job%2Fa");
  });
  it("accepts pending research without leads and preserves completed analysis", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ id: "job1", status: "queued" })),
        )
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              id: "job1",
              status: "completed",
              leads: [],
              analysis: {
                answer: "Live report",
                sources: [],
                model: "Cortex",
                usage: { tokens: 10 },
              },
            }),
          ),
        ),
    );
    expect((await api.researchResult("job1")).leads).toEqual([]);
    expect((await api.researchResult("job1")).analysis?.answer).toBe(
      "Live report",
    );
  });
});

describe("bounded polling", () => {
  it.each(["partial", "PARTIAL"])(
    "returns %s sources and warning without further polling",
    async (status) => {
      const result = {
        id: "j1",
        status,
        leads: [
          { id: "l1", title: "Saved source", url: "https://example.org" },
        ],
        error:
          "Sources were saved, but the AI analysis could not be verified. You can inspect the sources or ask a follow-up question.",
      };
      const fetch = vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify(result)));
      vi.stubGlobal("fetch", fetch);
      const update = vi.fn();
      await expect(
        pollResearch("j1", new AbortController().signal, update, 0, 3),
      ).resolves.toEqual(result);
      expect(fetch).toHaveBeenCalledOnce();
      expect(update).toHaveBeenCalledExactlyOnceWith(status);
    },
  );
  it("returns server leads only after completion", async () => {
    vi.spyOn(api, "researchResult")
      .mockResolvedValueOnce({ id: "j1", status: "pending", leads: [] })
      .mockResolvedValueOnce({
        id: "j1",
        status: "completed",
        leads: [
          { id: "l1", title: "Returned lead", url: "https://example.org" },
        ],
      });
    const update = vi.fn();
    expect(
      (await pollResearch("j1", new AbortController().signal, update, 0, 2))
        .leads,
    ).toHaveLength(1);
    expect(update.mock.calls.map((call) => call[0])).toEqual([
      "pending",
      "completed",
    ]);
  });
  it("stops at the pending budget", async () => {
    const result = vi
      .spyOn(api, "researchResult")
      .mockResolvedValue({ id: "j1", status: "pending", leads: [] });
    await expect(
      pollResearch("j1", new AbortController().signal, () => {}, 0, 3),
    ).rejects.toThrow("longer than expected");
    expect(result).toHaveBeenCalledTimes(3);
  });
  it("surfaces job errors and refuses mismatched jobs", async () => {
    vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "j1",
      status: "failed",
      leads: [],
      error: "Provider unavailable",
    });
    await expect(
      pollResearch("j1", new AbortController().signal, () => {}),
    ).rejects.toThrow("Provider unavailable");
    vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "other",
      status: "done",
      leads: [],
    });
    await expect(
      pollResearch("j1", new AbortController().signal, () => {}),
    ).rejects.toThrow("different job");
  });
  it("rejects unsupported states without polling indefinitely", async () => {
    vi.spyOn(api, "researchResult").mockResolvedValue({
      id: "j1",
      status: "unknown",
      leads: [],
    });
    await expect(
      pollResearch("j1", new AbortController().signal, () => {}),
    ).rejects.toThrow("Unrecognized");
  });
  it("aborts during the polling delay", async () => {
    vi.useFakeTimers();
    const result = vi
      .spyOn(api, "researchResult")
      .mockResolvedValue({ id: "j1", status: "running", leads: [] });
    const controller = new AbortController();
    const promise = pollResearch("j1", controller.signal, () => {});
    const assertion = expect(promise).rejects.toThrow("Cancelled");
    await Promise.resolve();
    controller.abort();
    await assertion;
    expect(result).toHaveBeenCalledTimes(1);
  });
});

it("allows only absolute web source links", () => {
  expect(safeUrl("https://example.org/listing")).toBe(
    "https://example.org/listing",
  );
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,unsafe",
    "/local",
    "file:///private",
    "not a url",
  ])
    expect(safeUrl(url)).toBeUndefined();
});
