import type { Provider, ProviderTestResult } from "@/lib/api/types";

export type TestState = { result: ProviderTestResult } | { error: string };

export interface TestOutcome {
  tone: "ok" | "warn" | "danger";
  title: string;
  /** What the endpoint or the platform said, verbatim. */
  detail: string | null;
  facts: string[];
}

/** Reads a test as an Admin needs it: did it work, and if not, what exactly went wrong. */
export function describeTest(state: TestState, provider: Pick<Provider, "model">): TestOutcome {
  if ("error" in state) {
    return { tone: "danger", title: "The test could not run", detail: state.error, facts: [] };
  }
  const r = state.result;
  const facts = [r.latency_ms != null && `${Math.round(r.latency_ms)} ms`, r.dimension != null && `${r.dimension}-dimension vectors`].filter(Boolean) as string[];
  if (!r.connected) {
    return { tone: "danger", title: r.message || "Could not connect", detail: r.detail, facts };
  }
  if (r.model_available === false) {
    return { tone: "warn", title: `Connected, but the endpoint does not serve ${provider.model}`, detail: r.detail ?? r.message, facts };
  }
  if (r.generation_ok === false || r.embedding_ok === false) {
    return { tone: "warn", title: `Connected, but ${r.embedding_ok === false ? "embedding" : "generation"} failed`, detail: r.detail ?? r.message, facts };
  }
  return { tone: "ok", title: r.message || "Connected", detail: null, facts };
}
