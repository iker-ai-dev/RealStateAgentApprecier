import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { streamResearch } from "./researchStream";

class TestStream extends EventTarget {
  static current: TestStream;
  close = vi.fn();
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    super();
    TestStream.current = this;
  }
  progress(data: unknown) {
    this.dispatchEvent(
      new MessageEvent("progress", { data: JSON.stringify(data) }),
    );
  }
}
beforeEach(() => vi.stubGlobal("EventSource", TestStream));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("delivers intermediate sources and closes on the verified final snapshot", async () => {
  const update = vi.fn();
  const result = streamResearch("job", new AbortController().signal, update);
  TestStream.current.progress({
    id: "job",
    status: "running",
    stage: "analyzing",
    leads: [{ id: "l1", title: "Live source", url: "https://example.org" }],
  });
  expect(update).toHaveBeenCalledWith(
    expect.objectContaining({ stage: "analyzing", leads: expect.any(Array) }),
  );
  TestStream.current.progress({
    id: "job",
    status: "completed",
    leads: [],
    analysis: { answer: "Checked answer", sources: [] },
  });
  expect((await result).analysis?.answer).toBe("Checked answer");
  expect(TestStream.current.close).toHaveBeenCalledOnce();
});

it("resumes reads of the same job after transport failure without resubmitting", async () => {
  const read = vi
    .spyOn(api, "researchResult")
    .mockResolvedValue({ id: "job", status: "completed", leads: [] });
  const create = vi.spyOn(api, "research");
  const result = streamResearch("job", new AbortController().signal, vi.fn());
  TestStream.current.onerror?.();
  await result;
  expect(read).toHaveBeenCalledOnce();
  expect(create).not.toHaveBeenCalled();
  expect(TestStream.current.close).toHaveBeenCalledOnce();
});

it("rejects mismatched jobs without falling back or revealing them", async () => {
  const update = vi.fn();
  const result = streamResearch("job", new AbortController().signal, update);
  TestStream.current.progress({ id: "other", status: "completed", leads: [] });
  await expect(result).rejects.toThrow("different job");
  expect(update).not.toHaveBeenCalled();
});

it("cancels the listener and ignores late snapshots", async () => {
  const controller = new AbortController();
  const update = vi.fn();
  const result = streamResearch("job", controller.signal, update);
  controller.abort();
  await expect(result).rejects.toThrow("Cancelled");
  TestStream.current.progress({ id: "job", status: "completed", leads: [] });
  expect(update).not.toHaveBeenCalled();
  expect(TestStream.current.close).toHaveBeenCalledOnce();
});

it("retains partial sources without inventing an answer", async () => {
  const result = streamResearch("job", new AbortController().signal, vi.fn());
  TestStream.current.progress({
    id: "job",
    status: "partial",
    leads: [{ id: "l1", title: "Source", url: "https://example.org" }],
    error: "Evidence check failed",
  });
  expect(await result).toMatchObject({
    status: "partial",
    error: "Evidence check failed",
    leads: [{ id: "l1" }],
  });
});
