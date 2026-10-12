import { z } from "zod";

export const leadSchema = z.object({
  id: z.string(),
  title: z.string(),
  url: z.string(),
  location: z.string().nullish(),
  summary: z.string().nullish(),
  status: z.string().nullish(),
  retrieved_at: z.string().nullish(),
});
export type Lead = z.infer<typeof leadSchema>;
export const statusSchema = z.object({
  providers: z.object({ snowflake: z.boolean(), nimble: z.boolean() }),
  model: z.string(),
});
export type ProviderStatus = z.infer<typeof statusSchema>;
const analysisSchema = z.object({
  answer: z.string(),
  sources: z.array(z.object({ title: z.string(), url: z.string() })),
  usage: z.unknown().optional(),
  model: z.string().optional(),
});
const researchSchema = z.object({
  id: z.string(),
  status: z.string(),
  leads: z.array(leadSchema).default([]),
  error: z.string().nullish(),
  analysis: analysisSchema.nullish(),
});
export type ResearchResult = z.infer<typeof researchSchema>;
const chatSchema = z.object({
  answer: z.string(),
  sources: z.array(z.object({ title: z.string(), url: z.string() })),
  conversation_id: z.string(),
  usage: z.unknown().optional(),
});
export type ChatResult = z.infer<typeof chatSchema>;
export type ScenarioInput = {
  purchase_price: number;
  annual_noi: number;
  exit_cap_rate: number;
  hold_years?: number;
  loan_to_value?: number;
  interest_rate?: number;
  noi_growth?: number;
};
const scenarioSchema = z.object({
  irr: z.number().finite().nullable(),
  equity_multiple: z.number().finite(),
  initial_equity: z.number().finite(),
  annual_cash_flows: z.array(
    z.object({
      year: z.number(),
      noi: z.number().finite(),
      debt_service: z.number().finite(),
      operating_cash_flow: z.number().finite(),
      equity_cash_flow: z.number().finite(),
    }),
  ),
  assumptions: z.record(z.unknown()),
  basis: z.string().optional(),
  exclusions: z.array(z.string()).optional(),
});
export type ScenarioResult = z.infer<typeof scenarioSchema>;
export type CalculatedScenario = {
  input: ScenarioInput;
  result: ScenarioResult;
};
const conversationsSchema = z.object({
  conversations: z.array(
    z.object({
      id: z.string(),
      updated_at: z.union([z.string(), z.number()]).nullish(),
      preview: z.string(),
    }),
  ),
});
export type ConversationSummary = z.infer<
  typeof conversationsSchema
>["conversations"][number];

export function safeUrl(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

async function request<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  signal?: AbortSignal,
  body?: unknown,
): Promise<z.output<S>> {
  const timeout = AbortSignal.timeout(30_000);
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string")
        detail = payload.detail.slice(0, 500);
    } catch {
      /* Gateways may return non-JSON errors. */
    }
    throw new Error(
      detail || `Request failed (${response.status}). Please try again.`,
    );
  }
  let json: unknown;
  try {
    json = await response.json();
  } catch {
    throw new Error("The API returned an unreadable response.");
  }
  const result = schema.safeParse(json);
  if (!result.success)
    throw new Error("The API response did not match the expected format.");
  return result.data;
}

export const api = {
  status: (signal?: AbortSignal) => request("/status", statusSchema, signal),
  leads: (signal?: AbortSignal) =>
    request("/leads", z.object({ leads: z.array(leadSchema) }), signal),
  research: (query: string, signal?: AbortSignal) =>
    request(
      "/research",
      z.object({ id: z.string(), status: z.string() }),
      signal,
      { query },
    ),
  researchResult: (id: string, signal?: AbortSignal) =>
    request(`/research/${encodeURIComponent(id)}`, researchSchema, signal),
  chat: (
    body: {
      message: string;
      lead_id?: string;
      conversation_id?: string;
      scenario?: ScenarioInput;
    },
    signal?: AbortSignal,
  ) => request("/chat", chatSchema, signal, body),
  scenario: (body: ScenarioInput, signal?: AbortSignal) =>
    request("/scenarios", scenarioSchema, signal, body),
  mcpToken: (signal?: AbortSignal) =>
    request(
      "/mcp-token",
      z.object({
        token: z.string().min(1),
        expires_at: z.number().int().positive(),
      }),
      signal,
      {},
    ),
  conversations: (signal?: AbortSignal) =>
    request("/conversations", conversationsSchema, signal),
  conversation: (id: string, signal?: AbortSignal) =>
    request(
      `/conversations/${encodeURIComponent(id)}`,
      z.object({
        id: z.string(),
        messages: z.array(
          z.object({
            role: z.enum(["user", "assistant"]),
            content: z.string(),
          }),
        ),
      }),
      signal,
    ),
};

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

// Finite polling budget: a slow or unknown server state cannot poll indefinitely.
export async function pollResearch(
  id: string,
  signal: AbortSignal,
  onStatus: (status: string) => void,
  interval = 2000,
  attempts = 45,
): Promise<ResearchResult> {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    const result = await api.researchResult(id, signal);
    if (result.id !== id)
      throw new Error("Research response belongs to a different job.");
    onStatus(result.status);
    const status = result.status.toLowerCase();
    if (
      ["completed", "complete", "done", "succeeded", "success"].includes(status)
    )
      return result;
    if (["failed", "error", "cancelled", "canceled"].includes(status))
      throw new Error(result.error || "Research could not be completed.");
    if (
      !["pending", "queued", "running", "processing", "in_progress"].includes(
        status,
      )
    )
      throw new Error(`Unrecognized research status: ${result.status}`);
    if (attempt < attempts - 1) await delay(interval, signal);
  }
  throw new Error("Research is taking longer than expected. Try again later.");
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.name === "TimeoutError")
    return "The request timed out. Please try again.";
  if (error instanceof TypeError)
    return "Cannot reach the API. Check your connection and try again.";
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
