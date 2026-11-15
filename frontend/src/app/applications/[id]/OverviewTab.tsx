"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Callout, Icon, StatusBadge, Toggle, useToast } from "@/components/ui";
import { applicationsApi, systemApi } from "@/lib/api/resources";
import type { Application } from "@/lib/api/types";
import { ModelFields, type ModelValue } from "../ModelFields";

interface Check {
  label: string;
  ok: boolean;
  remedy: string;
}

function useReadinessChecks(app: Application): Check[] {
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health, refetchInterval: 15000 });
  const keys = useQuery({ queryKey: ["app-keys", app.id], queryFn: () => applicationsApi.keys(app.id) });
  const inference = health.data?.components.find((c) => c.name === "Inference");
  return [
    {
      label: "Inference reachable",
      ok: inference?.status === "healthy",
      remedy: inference?.detail || "The inference provider is not responding. Check it is running.",
    },
    { label: "Model configured", ok: !!app.model_configuration?.llm_model, remedy: "No model is set on this Application." },
    {
      label: "Knowledge indexed",
      ok: app.knowledge_bases.some((kb) => kb.chunk_count > 0),
      remedy:
        app.knowledge_bases.length === 0
          ? "No Knowledge Base is attached, so it cannot answer anything."
          : "The attached Knowledge Bases have no indexed passages yet. Upload a Document.",
    },
    { label: "API key active", ok: (keys.data ?? []).some((k) => k.is_active), remedy: "No active key. Create one under API access." },
  ];
}

function Readiness({ app }: { app: Application }) {
  const checks = useReadinessChecks(app);
  const ready = checks.every((c) => c.ok);
  return (
    <section aria-labelledby="readiness-heading" className="card p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="readiness-heading" className="font-display text-lg font-semibold">
          Readiness
        </h2>
        <StatusBadge status={ready ? "Ready" : "Needs attention"} tone={ready ? "ok" : "warn"} />
      </div>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {checks.map((c) => (
          <li key={c.label} className={`rounded-control border p-3 ${c.ok ? "border-ok/25 bg-ok-soft" : "border-clay/30 bg-clay-soft"}`}>
            <div className="text-sm font-medium text-fg">
              {c.label}
              <span className="sr-only">: {c.ok ? "yes" : "no"}</span>
            </div>
            <p className="mt-0.5 text-xs text-muted">{c.ok ? "OK" : c.remedy}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function OverviewTab({ app, onDelete }: { app: Application; onDelete: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const mc = app.model_configuration;
  const [name, setName] = useState(app.name);
  const [description, setDescription] = useState(app.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(app.system_prompt ?? "");
  const [citations, setCitations] = useState(app.citations_enabled);
  const [streaming, setStreaming] = useState(app.streaming_enabled);
  const [model, setModel] = useState<ModelValue>({
    llmProviderId: "",
    llmProvider: mc?.llm_provider ?? "ollama",
    llmEndpoint: mc?.llm_endpoint ?? "",
    llmApiKeyRef: mc?.llm_api_key_ref?.startsWith("env:") ? mc.llm_api_key_ref : "",
    llmModel: mc?.llm_model ?? "",
  });

  const save = useMutation({
    mutationFn: () =>
      applicationsApi.update(app.id, {
        name: name.trim(),
        description,
        system_prompt: systemPrompt,
        citations_enabled: citations,
        streaming_enabled: streaming,
        llm_provider: model.llmProvider,
        llm_endpoint: model.llmEndpoint,
        llm_api_key_ref: model.llmApiKeyRef,
        llm_model: model.llmModel,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["app", app.id] });
      qc.invalidateQueries({ queryKey: ["apps"] });
      toast({ title: "Application saved" });
    },
  });

  return (
    <div className="space-y-6">
      <Readiness app={app} />

      <form
        className="card space-y-4 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <h2 className="font-display text-lg font-semibold">Settings</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="ov-name">
              Name
            </label>
            <input id="ov-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
            <p className="mt-1.5 text-xs text-muted">
              Model name for the API: <code className="rounded-sm bg-sunken px-1.5 py-0.5 text-fg">{app.slug}</code>. It does not change when you rename.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="ov-description">
              Description
            </label>
            <input id="ov-description" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="ov-prompt">
            System prompt
          </label>
          <textarea id="ov-prompt" className="input" rows={4} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} placeholder="Default prompt" />
        </div>
        <div className="max-w-xl">
          <ModelFields value={model} onChange={(p) => setModel((m) => ({ ...m, ...p }))} />
        </div>
        <Toggle checked={citations} onChange={setCitations} label="Return Citations" description="Answers point to the passages they came from." />
        <Toggle checked={streaming} onChange={setStreaming} label="Allow streaming" description="Answers appear word by word instead of all at once." />
        {save.isError && (
          <p role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </p>
        )}
        <div>
          <button type="submit" className="btn btn-primary" disabled={save.isPending || !name.trim() || !model.llmModel.trim()}>
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
        </div>
      </form>

      <section className="card border-danger/30 p-5">
        <h2 className="font-display text-lg font-semibold text-danger">Delete this Application</h2>
        <p className="mt-1 text-sm text-muted">Removes the Application and its API keys. Knowledge Bases and Documents stay.</p>
        <button type="button" className="btn btn-danger mt-3" onClick={onDelete}>
          <Icon icon={Trash2} size="sm" />
          Delete Application
        </button>
      </section>

      {!app.knowledge_bases.length && (
        <Callout tone="warn" title="No Knowledge Base attached">
          Attach at least one under the Knowledge Bases tab, or this Application has nothing to answer from.
        </Callout>
      )}
    </div>
  );
}
