"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorState, PageHeader, Toggle } from "@/components/ui";
import { applicationsApi, knowledgeBasesApi } from "@/lib/api/resources";
import type { ApplicationCreate } from "@/lib/api/types";
import { slugify } from "@/lib/slug";
import { KnowledgeBasePicker } from "../KnowledgeBasePicker";
import { ModelFields, type ModelValue } from "../ModelFields";

interface Form extends ModelValue {
  name: string;
  description: string;
  systemPrompt: string;
  kbIds: string[];
  temperature: number;
  maxTokens: number;
  citationsEnabled: boolean;
  streamingEnabled: boolean;
}

const INITIAL: Form = {
  name: "",
  description: "",
  systemPrompt: "",
  kbIds: [],
  llmProviderId: "",
  llmProvider: "ollama",
  llmEndpoint: "",
  llmApiKeyRef: "",
  llmModel: "llama3.2:1b",
  temperature: 0,
  maxTokens: 1024,
  citationsEnabled: true,
  streamingEnabled: true,
};

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <h2 className="font-display text-lg font-semibold text-fg">{title}</h2>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

export default function NewApplicationPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [form, setForm] = useState<Form>(INITIAL);
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));

  const apps = useQuery({ queryKey: ["apps"], queryFn: applicationsApi.list });
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });
  const slug = slugify(form.name);
  const named = form.name.trim() !== "";
  const slugTaken = named && !!apps.data?.some((a) => a.slug === slug);
  const attached = (kbs.data ?? []).filter((kb) => form.kbIds.includes(kb.id));
  const documents = attached.reduce((sum, kb) => sum + kb.document_count, 0);
  const canCreate = named && !slugTaken && form.llmModel.trim() !== "";

  const create = useMutation({
    mutationFn: () => {
      const payload: ApplicationCreate = {
        name: form.name.trim(),
        description: form.description || undefined,
        system_prompt: form.systemPrompt || undefined,
        rag_strategy: "traditional",
        knowledge_base_ids: form.kbIds,
        llm_provider: form.llmProvider,
        llm_endpoint: form.llmEndpoint || undefined,
        llm_api_key_ref: form.llmApiKeyRef || undefined,
        llm_model: form.llmModel,
        temperature: form.temperature,
        max_tokens: form.maxTokens,
        citations_enabled: form.citationsEnabled,
        streaming_enabled: form.streamingEnabled,
      };
      return applicationsApi.create(payload);
    },
    onSuccess: (app) => {
      qc.invalidateQueries({ queryKey: ["apps"] });
      // The plaintext key exists only on this response. Hand it to the API access tab in memory
      // (never storage) so it can be shown once.
      if (app.api_key) (window as unknown as { __ragNewKey?: string }).__ragNewKey = app.api_key;
      router.push(`/applications/${app.id}?tab=api`);
    },
  });

  return (
    <div>
      <PageHeader title="New Application" description="Everything on one page. The summary on the right updates as you go." />

      <form
        className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]"
        onSubmit={(e) => {
          e.preventDefault();
          if (canCreate) create.mutate();
        }}
      >
        <div className="space-y-5">
          <Section title="Name">
            <div>
              <label className="label" htmlFor="app-name">
                Name
              </label>
              <input id="app-name" className="input" autoFocus value={form.name} placeholder="HR policies" onChange={(e) => patch({ name: e.target.value })} />
              {named && !slugTaken && (
                <p className="mt-1.5 text-xs text-muted">
                  Model name for the API: <code className="rounded-sm bg-sunken px-1.5 py-0.5 text-fg">{slug}</code>
                </p>
              )}
              {slugTaken && (
                <p role="alert" className="mt-1.5 text-xs text-danger">
                  An Application with this name already exists. Choose another.
                </p>
              )}
            </div>
            <div>
              <label className="label" htmlFor="app-description">
                Description (optional)
              </label>
              <input id="app-description" className="input" value={form.description} placeholder="Leave, expenses and conduct" onChange={(e) => patch({ description: e.target.value })} />
              <p className="mt-1.5 text-xs text-muted">People see this under the Application&apos;s name when they choose it in the chat.</p>
            </div>
          </Section>

          <Section title="Knowledge Bases" hint="The Documents this Application may draw on. Who can use it still depends on their Grants.">
            <KnowledgeBasePicker selected={form.kbIds} onChange={(kbIds) => patch({ kbIds })} />
          </Section>

          <Section title="Prompt and model">
            <div>
              <label className="label" htmlFor="app-prompt">
                System prompt (optional)
              </label>
              <textarea
                id="app-prompt"
                className="input"
                rows={4}
                value={form.systemPrompt}
                placeholder="You answer questions for Acme employees from the attached documents. Say so when the documents do not cover the question."
                onChange={(e) => patch({ systemPrompt: e.target.value })}
              />
            </div>
            <ModelFields value={form} onChange={patch} />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="app-temperature">
                  Temperature
                </label>
                <input id="app-temperature" className="input" type="number" step="0.1" min={0} max={2} value={form.temperature} onChange={(e) => patch({ temperature: Number(e.target.value) })} />
              </div>
              <div>
                <label className="label" htmlFor="app-max-tokens">
                  Maximum answer length (tokens)
                </label>
                <input id="app-max-tokens" className="input" type="number" min={1} max={32768} value={form.maxTokens} onChange={(e) => patch({ maxTokens: Number(e.target.value) })} />
              </div>
            </div>
            <Toggle checked={form.citationsEnabled} onChange={(citationsEnabled) => patch({ citationsEnabled })} label="Return Citations" description="Answers point to the passages they came from." />
            <Toggle checked={form.streamingEnabled} onChange={(streamingEnabled) => patch({ streamingEnabled })} label="Allow streaming" description="Answers appear word by word instead of all at once." />
          </Section>

          {create.isError && <ErrorState title="Could not create the Application" message={(create.error as Error).message} />}
        </div>

        <aside aria-label="Summary" className="xl:sticky xl:top-6 xl:self-start">
          <div className="card p-5">
            <h2 className="font-display text-lg font-semibold text-fg">Summary</h2>
            <p className="mt-3 text-sm text-fg">
              {named ? <strong>{form.name.trim()}</strong> : <span className="text-muted">This Application</span>}{" "}
              {attached.length === 0 ? (
                <span className="text-clay-text">has no Knowledge Base yet, so it cannot answer anything.</span>
              ) : (
                <>
                  will answer from <strong>{documents}</strong> Document{documents === 1 ? "" : "s"} in {attached.length} Knowledge Base{attached.length === 1 ? "" : "s"}
                </>
              )}
              {attached.length > 0 && <>, using <strong className="font-mono">{form.llmModel || "no model"}</strong>.</>}
            </p>
            <dl className="mt-4 space-y-2.5 border-t border-line pt-4 text-sm">
              {[
                ["Model name (API)", named ? slug : "—"],
                ["Knowledge Bases", attached.length ? attached.map((kb) => kb.name).join(", ") : "None"],
                ["Model", form.llmModel || "—"],
                ["Prompt", form.systemPrompt.trim() ? "Custom" : "Default"],
                ["Citations", form.citationsEnabled ? "On" : "Off"],
                ["Streaming", form.streamingEnabled ? "On" : "Off"],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4">
                  <dt className="shrink-0 text-muted">{label}</dt>
                  <dd className="min-w-0 text-right font-medium text-fg [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
            <button type="submit" className="btn btn-primary mt-5 w-full" disabled={!canCreate || create.isPending}>
              {create.isPending ? "Creating…" : "Create Application"}
            </button>
            <p className="mt-2 text-xs text-muted">An API key is issued with it and shown once on the next screen.</p>
          </div>
        </aside>
      </form>
    </div>
  );
}
