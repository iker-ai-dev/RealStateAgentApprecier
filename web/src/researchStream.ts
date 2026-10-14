import { pollResearch, researchSchema, type ResearchResult } from "./api";
import { requestSignal } from "./requestSignal";

class StreamUnavailable extends Error {}

export const stageLabel = (stage: string) =>
  ({
    queued: "Getting started",
    planning: "Planning the investigation",
    inspecting: "Inspecting source details",
    followup: "Following up on evidence gaps",
    reading: "Reading source details",
    investigating: "Investigating evidence gaps",
    following_up: "Following up on missing evidence",
    searching: "Searching public listings",
    running: "Researching listings",
    analyzing: "Analyzing retrieved evidence",
    validating: "Checking evidence and citations",
    repairing: "Rechecking the analysis",
    completed: "Research complete",
    partial: "Sources saved; analysis needs attention",
    failed: "Research could not finish",
  })[stage] || stage.replaceAll("_", " ");

export async function streamResearch(
  id: string,
  signal: AbortSignal,
  onUpdate: (result: ResearchResult) => void,
): Promise<ResearchResult> {
  const pending = requestSignal(signal, 630_000);
  try {
    if (typeof EventSource !== "undefined") {
      try {
        return await new Promise<ResearchResult>((resolve, reject) => {
          const stream = new EventSource(
            `/api/research/${encodeURIComponent(id)}/events`,
          );
          let settled = false;
          const finish = (result?: ResearchResult, error?: Error) => {
            if (settled) return;
            settled = true;
            stream.close();
            clearTimeout(idle);
            pending.signal.removeEventListener("abort", abort);
            if (error) reject(error);
            else resolve(result!);
          };
          const abort = () =>
            finish(undefined, new DOMException("Cancelled", "AbortError"));
          let idle = window.setTimeout(
            () => finish(undefined, new StreamUnavailable()),
            25000,
          );
          const alive = () => {
            clearTimeout(idle);
            idle = window.setTimeout(
              () => finish(undefined, new StreamUnavailable()),
              25000,
            );
          };
          stream.addEventListener("heartbeat", alive);
          stream.addEventListener("progress", (event) => {
            if (settled) return;
            alive();
            try {
              const result = researchSchema.parse(
                JSON.parse((event as MessageEvent).data),
              );
              if (result.id !== id)
                throw new Error(
                  "Research response belongs to a different job.",
                );
              onUpdate(result);
              if (["completed", "partial"].includes(result.status))
                finish(result);
              else if (["failed", "cancelled"].includes(result.status))
                finish(
                  undefined,
                  new Error(result.error || "Research could not finish."),
                );
            } catch (error) {
              finish(
                undefined,
                error instanceof Error
                  ? error
                  : new Error("Invalid research stream."),
              );
            }
          });
          stream.onerror = () => finish(undefined, new StreamUnavailable());
          pending.signal.addEventListener("abort", abort, { once: true });
          if (pending.signal.aborted) abort();
        });
      } catch (error) {
        if (!(error instanceof StreamUnavailable) || pending.signal.aborted)
          throw error;
      }
    }
    // A dropped stream only resumes reading the same job; never resubmit research.
    return await pollResearch(
      id,
      pending.signal,
      () => {},
      2000,
      316,
      onUpdate,
    );
  } finally {
    pending.dispose();
  }
}
