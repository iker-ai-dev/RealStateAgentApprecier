import { afterEach, expect, it, vi } from "vitest";
import { requestSignal } from "./requestSignal";
import { api } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("supports chat when modern AbortSignal static methods are unavailable", async () => {
  vi.spyOn(AbortSignal, "any").mockImplementation(() => {
    throw new TypeError("Unsupported");
  });
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => {
    throw new TypeError("Unsupported");
  });
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(
        JSON.stringify({
          answer: "Real response",
          sources: [],
          conversation_id: "c1",
        }),
      ),
    );
  vi.stubGlobal("fetch", fetch);
  expect(
    (await api.chat({ message: "Evidence?" }, new AbortController().signal))
      .answer,
  ).toBe("Real response");
  expect(fetch).toHaveBeenCalledOnce();
});

it("propagates cancellation and already-cancelled parents", () => {
  const parent = new AbortController();
  const pending = requestSignal(parent.signal, 1000);
  parent.abort();
  expect(pending.signal.aborted).toBe(true);
  const second = requestSignal(parent.signal, 1000);
  expect(second.signal.aborted).toBe(true);
  pending.dispose();
  second.dispose();
});

it("times out with a distinct reason and cleans up timers", () => {
  vi.useFakeTimers();
  const pending = requestSignal(undefined, 1000);
  vi.advanceTimersByTime(1000);
  expect(pending.signal.reason.name).toBe("TimeoutError");
  pending.dispose();
  const finished = requestSignal(undefined, 1000);
  finished.dispose();
  vi.advanceTimersByTime(1000);
  expect(finished.signal.aborted).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});
