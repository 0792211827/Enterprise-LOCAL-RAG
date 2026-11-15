"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Modal, Toggle } from "@/components/ui";
import { providersApi } from "@/lib/api/resources";
import type { Provider, ProviderCreate, ProviderKind, ProviderType, ProviderUpdate } from "@/lib/api/types";

export const KIND_LABELS: Record<string, string> = { llm: "Language model", embedding: "Embedding", vlm: "Vision" };
export const TYPE_LABELS: Record<string, string> = { ollama: "Ollama", "openai-compatible": "OpenAI-compatible", huggingface: "HuggingFace" };

const DEFAULT_ENDPOINTS: Record<ProviderType, string> = {
  ollama: "http://ollama:11434",
  "openai-compatible": "http://inference.internal:8000/v1",
  huggingface: "",
};

const URL_PATTERN = /^https?:\/\/\S+$/i;

interface Form {
  name: string;
  kind: ProviderKind;
  provider_type: ProviderType;
  endpoint: string;
  model: string;
  api_key_ref: string;
  dimension: string;
  enabled: boolean;
}

const fromProvider = (p: Provider | null): Form => ({
  name: p?.name ?? "",
  kind: (p?.kind as ProviderKind) ?? "llm",
  provider_type: (p?.provider_type as ProviderType) ?? "ollama",
  endpoint: p ? (p.endpoint ?? "") : DEFAULT_ENDPOINTS.ollama,
  model: p?.model ?? "",
  api_key_ref: "",
  dimension: p?.dimension ?? "",
  enabled: p?.enabled ?? true,
});

/** Add an Inference endpoint, or edit one. The kind and type are fixed once it exists. */
export function ProviderForm({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const qc = useQueryClient();
  const editing = provider !== null;
  const [form, setForm] = useState<Form>(() => fromProvider(provider));
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));

  const endpointInvalid = form.endpoint.trim() !== "" && !URL_PATTERN.test(form.endpoint.trim());
  const valid = form.name.trim() !== "" && form.model.trim() !== "" && !endpointInvalid;

  const save = useMutation({
    mutationFn: () => {
      const common = {
        name: form.name.trim(),
        endpoint: form.endpoint.trim(),
        model: form.model.trim(),
        enabled: form.enabled,
        ...(form.api_key_ref.trim() && { api_key_ref: form.api_key_ref.trim() }),
        ...(form.kind === "embedding" && form.dimension.trim() && { dimension: form.dimension.trim() }),
      };
      if (provider) return providersApi.update(provider.id, common satisfies ProviderUpdate);
      const payload: ProviderCreate = { ...common, kind: form.kind, provider_type: form.provider_type };
      // An empty endpoint is left to the platform's default rather than stored as an empty string.
      if (!payload.endpoint) delete payload.endpoint;
      return providersApi.create(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      onClose();
    },
  });

  return (
    <Modal open onClose={onClose} title={editing ? `Edit ${provider.name}` : "Add Inference endpoint"} size="lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate();
        }}
      >
        <div>
          <label className="label" htmlFor="provider-name">
            Name
          </label>
          <input id="provider-name" className="input" autoFocus value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="Local Ollama" />
        </div>

        {editing ? (
          <p className="text-sm text-muted">
            {KIND_LABELS[form.kind] ?? form.kind} · {TYPE_LABELS[form.provider_type] ?? form.provider_type}. The kind and type cannot change once an endpoint exists. Add a new one instead.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="provider-kind">
                What it does
              </label>
              <select id="provider-kind" className="input" value={form.kind} onChange={(e) => patch({ kind: e.target.value as ProviderKind })}>
                {Object.entries(KIND_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="provider-type">
                Type
              </label>
              <select
                id="provider-type"
                className="input"
                value={form.provider_type}
                onChange={(e) => {
                  const provider_type = e.target.value as ProviderType;
                  // Swap the suggested address only while the person has not typed their own.
                  const untouched = Object.values(DEFAULT_ENDPOINTS).includes(form.endpoint);
                  patch({ provider_type, ...(untouched && { endpoint: DEFAULT_ENDPOINTS[provider_type] }) });
                }}
              >
                {Object.entries(TYPE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        <div>
          <label className="label" htmlFor="provider-endpoint">
            Address
          </label>
          <input
            id="provider-endpoint"
            className="input font-mono"
            value={form.endpoint}
            aria-invalid={endpointInvalid}
            aria-describedby="provider-endpoint-help"
            onChange={(e) => patch({ endpoint: e.target.value })}
            placeholder="http://ollama:11434"
          />
          <p id="provider-endpoint-help" className={`mt-1.5 text-xs ${endpointInvalid ? "text-danger" : "text-muted"}`}>
            {endpointInvalid ? "Start with http:// or https://." : "Where the endpoint listens. It must be on the hosts the operator allows."}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="provider-model">
              Model
            </label>
            <input id="provider-model" className="input font-mono" value={form.model} onChange={(e) => patch({ model: e.target.value })} placeholder="llama3.2:1b" />
          </div>
          {form.kind === "embedding" && (
            <div>
              <label className="label" htmlFor="provider-dimension">
                Vector size (optional)
              </label>
              <input id="provider-dimension" className="input font-mono" value={form.dimension} onChange={(e) => patch({ dimension: e.target.value })} placeholder="768" />
            </div>
          )}
        </div>

        <div>
          <label className="label" htmlFor="provider-key">
            API key (optional)
          </label>
          <input
            id="provider-key"
            className="input font-mono"
            type="password"
            autoComplete="off"
            value={form.api_key_ref}
            onChange={(e) => patch({ api_key_ref: e.target.value })}
            placeholder={editing && provider.api_key_ref ? `Currently ${provider.api_key_ref}. Leave empty to keep it.` : "env:INFERENCE_API_KEY"}
          />
          <p className="mt-1.5 text-xs text-muted">Prefer env:NAME, which reads the key from the server&apos;s environment. A key typed here is stored but never shown again.</p>
        </div>

        <Toggle checked={form.enabled} onChange={(enabled) => patch({ enabled })} label="Enabled" description="A disabled endpoint stays in the list, marked Disabled." />

        {save.isError && (
          <p role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!valid || save.isPending}>
            {save.isPending ? "Saving…" : editing ? "Save changes" : "Add endpoint"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
