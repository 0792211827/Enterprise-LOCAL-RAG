"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Callout } from "@/components/ui";
import { providersApi } from "@/lib/api/resources";
import type { Provider, ProviderTestResult } from "@/lib/api/types";

export interface ModelValue {
  llmProviderId: string;
  llmProvider: string;
  llmEndpoint: string;
  llmApiKeyRef: string;
  llmModel: string;
}

/** The provider and model an Application answers with. Credentials are `env:NAME` references only. */
export function ModelFields({ value, onChange }: { value: ModelValue; onChange: (patch: Partial<ModelValue>) => void }) {
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const providers = useQuery({ queryKey: ["providers", "llm"], queryFn: () => providersApi.list("llm") });
  const models = useQuery({
    queryKey: ["provider-models", value.llmProviderId],
    queryFn: () => providersApi.models(value.llmProviderId),
    enabled: !!value.llmProviderId,
  });
  const selected = providers.data?.find((p) => p.id === value.llmProviderId);

  const choose = (p: Provider | undefined) => {
    setTest(null);
    if (!p) {
      onChange({ llmProviderId: "" });
      return;
    }
    onChange({
      llmProviderId: p.id,
      llmProvider: p.provider_type,
      llmEndpoint: p.endpoint ?? "",
      // Only a reference can be carried over; a literal key is never sent back to the browser.
      llmApiKeyRef: p.api_key_ref?.startsWith("env:") ? p.api_key_ref : "",
      llmModel: p.model || value.llmModel,
    });
  };

  const runTest = async () => {
    setTesting(true);
    try {
      setTest(await providersApi.test(value.llmProviderId));
    } catch (e) {
      setTest({
        connected: false,
        model_available: null,
        generation_ok: null,
        embedding_ok: null,
        dimension: null,
        latency_ms: null,
        message: (e as Error).message,
        detail: null,
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="label" htmlFor="model-provider">
          Provider
        </label>
        <select id="model-provider" className="input" value={value.llmProviderId} onChange={(e) => choose(providers.data?.find((p) => p.id === e.target.value))}>
          <option value="">Set by hand, not from a registered provider</option>
          {(providers.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.provider_type})
            </option>
          ))}
        </select>
        {providers.data?.length === 0 && (
          <p className="mt-1.5 text-xs text-muted">
            No language-model providers are registered.{" "}
            <Link href="/providers" className="text-accent-text underline">
              Add one
            </Link>{" "}
            or type a model name below.
          </p>
        )}
      </div>

      <div>
        <label className="label" htmlFor="model-name">
          Model
        </label>
        {models.data?.reachable && models.data.models.length > 0 ? (
          <select id="model-name" className="input" value={value.llmModel} onChange={(e) => onChange({ llmModel: e.target.value })}>
            {!models.data.models.some((m) => m.id === value.llmModel) && <option value={value.llmModel}>{value.llmModel}</option>}
            {models.data.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <input id="model-name" className="input font-mono" value={value.llmModel} onChange={(e) => onChange({ llmModel: e.target.value })} placeholder="llama3.2:1b" />
        )}
        {value.llmProviderId && models.data && !models.data.reachable && (
          <p className="mt-1.5 text-xs text-clay-text">
            Could not list models from this provider, so type the name.{models.data.detail ? ` (${models.data.detail})` : ""}
          </p>
        )}
      </div>

      {!value.llmProviderId && (
        <>
          <div>
            <label className="label" htmlFor="model-endpoint">
              Endpoint (optional)
            </label>
            <input id="model-endpoint" className="input font-mono" value={value.llmEndpoint} onChange={(e) => onChange({ llmEndpoint: e.target.value })} placeholder="http://ollama:11434" />
          </div>
          <div>
            <label className="label" htmlFor="model-key-ref">
              API key reference (optional)
            </label>
            <input id="model-key-ref" className="input font-mono" value={value.llmApiKeyRef} onChange={(e) => onChange({ llmApiKeyRef: e.target.value })} placeholder="env:OPENAI_API_KEY" />
            <p className="mt-1.5 text-xs text-muted">The name of an environment variable on the server, as env:NAME. Never paste the key itself.</p>
          </div>
        </>
      )}

      {selected && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn btn-secondary btn-sm" onClick={runTest} disabled={testing}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {test && (
            <span className={`text-xs ${test.connected ? "text-ok" : "text-danger"}`}>
              {test.message}
              {test.latency_ms != null && ` · ${test.latency_ms} ms`}
              {test.model_available === false && " · model not found on this provider"}
            </span>
          )}
        </div>
      )}
      {test && !test.connected && <Callout tone="warn">The provider did not answer. You can still save; the Application will fail to answer until it does.</Callout>}
    </div>
  );
}
