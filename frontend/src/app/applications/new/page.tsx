"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import {
  Advanced,
  Callout,
  ErrorState,
  PageHeader,
  Stepper,
} from "@/components/ui";
import {
  applicationsApi,
  documentsApi,
  knowledgeBasesApi,
  providersApi,
} from "@/lib/api/resources";
import type { ApplicationCreate, Provider, ProviderTestResult, RetrievalMode } from "@/lib/api/types";
import { slugify } from "@/lib/slug";
import { UploadRow, type UploadItem, type UploadState } from "./UploadRow";

const STEPS = ["Name", "Documents", "Model", "Review"];

interface Form {
  name: string;
  description: string;
  systemPrompt: string;
  kbId: string | null;
  llmProviderId: string;
  llmProvider: string;
  llmEndpoint: string;
  llmModel: string;
  retrievalMode: RetrievalMode;
  topK: number;
  scoreThreshold: string;
  temperature: number;
  maxTokens: number;
  citationsEnabled: boolean;
  streamingEnabled: boolean;
}

const INITIAL: Form = {
  name: "",
  description: "",
  systemPrompt: "",
  kbId: null,
  llmProviderId: "",
  llmProvider: "ollama",
  llmEndpoint: "",
  llmModel: "llama3.2:1b",
  retrievalMode: "hybrid",
  topK: 8,
  scoreThreshold: "",
  temperature: 0,
  maxTokens: 1024,
  citationsEnabled: true,
  streamingEnabled: true,
};

export default function NewAssistantPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Form>(INITIAL);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));

  const apps = useQuery({ queryKey: ["apps"], queryFn: applicationsApi.list });
  const slug = slugify(form.name);
  const slugTaken = !!apps.data?.some((a) => a.slug === slug) && form.name.trim() !== "";

  const onSettled = useCallback(
    (fileName: string, state: UploadState, error?: string) =>
      setUploads((u) => u.map((i) => (i.fileName === fileName ? { ...i, state, error } : i))),
    [],
  );

  const create = useMutation({
    mutationFn: () => {
      const payload: ApplicationCreate = {
        name: form.name,
        description: form.description || undefined,
        system_prompt: form.systemPrompt || undefined,
        rag_strategy: "traditional",
        knowledge_base_ids: form.kbId ? [form.kbId] : [],
        llm_provider: form.llmProvider,
        llm_endpoint: form.llmEndpoint || undefined,
        llm_model: form.llmModel,
        retrieval_mode: form.retrievalMode,
        top_k: form.topK,
        score_threshold: form.scoreThreshold ? Number(form.scoreThreshold) : undefined,
        temperature: form.temperature,
        max_tokens: form.maxTokens,
        citations_enabled: form.citationsEnabled,
        streaming_enabled: form.streamingEnabled,
      };
      return applicationsApi.create(payload);
    },
    onSuccess: (app) => {
      qc.invalidateQueries({ queryKey: ["apps"] });
      // The plaintext key exists only on this response. Hand it to the detail
      // page in memory (never sessionStorage) so it can be revealed once.
      if (app.api_key) {
        (window as unknown as { __ragNewKey?: string }).__ragNewKey = app.api_key;
      }
      router.push(`/applications/${app.id}?created=1`);
    },
  });

  const ingestedCount = uploads.filter((u) => u.state === "completed").length;

  // Same query key as StepDocuments, so React Query dedupes this — no extra
  // request. Needed here because picking a pre-populated knowledge base is a
  // documented way to finish this step, and that path ingests nothing.
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });
  const selectedKb = kbs.data?.find((k) => k.id === form.kbId);
  // Take the larger of the two: `ingestedCount` covers a just-finished upload
  // the cached list hasn't caught up with, `document_count` covers a knowledge
  // base that already had documents before this wizard ran.
  const documentCount = Math.max(ingestedCount, selectedKb?.document_count ?? 0);

  const canLeaveStep1 = form.name.trim().length > 0 && !slugTaken;
  const canLeaveStep2 = !!form.kbId && documentCount > 0;
  const canLeaveStep3 = form.llmModel.trim().length > 0;

  return (
    <div>
      <Link href="/applications" className="text-sm text-brand-600">
        ← Assistants
      </Link>
      <PageHeader
        title="New Assistant"
        description="Configure once, and leave with a callable endpoint."
      />

      <Stepper steps={STEPS} current={step} onStepClick={setStep} />

      <div className="card p-6">
        {step === 0 && (
          <StepName form={form} patch={patch} slug={slug} slugTaken={slugTaken} />
        )}
        {step === 1 && (
          <StepDocuments
            form={form}
            patch={patch}
            uploads={uploads}
            setUploads={setUploads}
            onSettled={onSettled}
          />
        )}
        {step === 2 && <StepModel form={form} patch={patch} />}
        {step === 3 && (
          <StepReview form={form} slug={slug} ingestedCount={documentCount} />
        )}

        {create.isError && (
          <div className="mt-4">
            <ErrorState message={(create.error as Error).message} />
          </div>
        )}

        <div className="mt-6 flex items-center justify-between border-t border-slate-100 pt-4">
          <button
            className="btn btn-secondary"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Back
          </button>
          {step < 3 ? (
            <button
              className="btn btn-primary"
              onClick={() => setStep((s) => s + 1)}
              disabled={
                (step === 0 && !canLeaveStep1) ||
                (step === 1 && !canLeaveStep2) ||
                (step === 2 && !canLeaveStep3)
              }
            >
              Continue
            </button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={() => create.mutate()}
              disabled={create.isPending}
            >
              {create.isPending ? "Creating…" : "Create assistant"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StepName({
  form,
  patch,
  slug,
  slugTaken,
}: {
  form: Form;
  patch: (p: Partial<Form>) => void;
  slug: string;
  slugTaken: boolean;
}) {
  return (
    <div className="max-w-xl space-y-4">
      <div>
        <label className="label">Name</label>
        <input
          className="input"
          autoFocus
          value={form.name}
          placeholder="HR Assistant"
          onChange={(e) => patch({ name: e.target.value })}
        />
        {form.name.trim() && (
          <p className="mt-2 text-xs text-slate-500">
            Model name:{" "}
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-700">
              {slug}
            </code>{" "}
            — this is what you pass as <code>model</code> when calling the endpoint.
          </p>
        )}
        {slugTaken && (
          <p className="mt-2 text-xs text-red-600">
            An assistant with this name already exists. Choose another.
          </p>
        )}
      </div>
      <div>
        <label className="label">Description (optional)</label>
        <input
          className="input"
          value={form.description}
          placeholder="Answers questions about HR policy"
          onChange={(e) => patch({ description: e.target.value })}
        />
      </div>
    </div>
  );
}

function StepDocuments({
  form,
  patch,
  uploads,
  setUploads,
  onSettled,
}: {
  form: Form;
  patch: (p: Partial<Form>) => void;
  uploads: UploadItem[];
  setUploads: React.Dispatch<React.SetStateAction<UploadItem[]>>;
  onSettled: (f: string, s: UploadState, e?: string) => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [kbError, setKbError] = useState<string | null>(null);
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });

  const ensureKb = async (): Promise<string> => {
    if (form.kbId) return form.kbId;
    // Knowledge-base names are unique; fall back to a suffixed name rather
    // than dead-ending the wizard on a 409.
    const base = `${form.name} Knowledge`;
    for (const name of [base, `${base} ${Date.now().toString().slice(-5)}`]) {
      try {
        const kb = await knowledgeBasesApi.create({ name });
        qc.invalidateQueries({ queryKey: ["kbs"] });
        patch({ kbId: kb.id });
        return kb.id;
      } catch (e) {
        if (!(e as Error).message.toLowerCase().includes("already exists")) throw e;
      }
    }
    throw new Error("Could not create a knowledge base for this assistant.");
  };

  const handleFiles = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setKbError(null);
    try {
      const kbId = await ensureKb();
      setUploads((u) => [
        ...u,
        ...files.map((f) => ({ fileName: f.name, state: "pending" as UploadState })),
      ]);
      // Sequential on purpose: ingestion runs on the API's event loop and
      // embedding is CPU-heavy, so parallel uploads stall the whole API.
      for (const file of files) {
        onSettled(file.name, "uploading");
        try {
          const doc = await documentsApi.upload(kbId, file);
          setUploads((u) =>
            u.map((i) =>
              i.fileName === file.name
                ? { ...i, state: "queued", documentId: doc.id }
                : i,
            ),
          );
        } catch (e) {
          const msg = (e as Error).message;
          const dup = msg.toLowerCase().includes("identical document");
          onSettled(file.name, dup ? "duplicate" : "failed", dup ? undefined : msg);
        }
      }
    } catch (e) {
      setKbError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const existingKbs = kbs.data ?? [];

  return (
    <div className="space-y-5">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(Array.from(e.dataTransfer.files));
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-10 text-center transition ${
          dragging ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-slate-50/50"
        }`}
      >
        <div className="text-sm font-medium text-slate-700">
          Drag and drop files here
        </div>
        <div className="text-xs text-slate-400">PDF, TXT or Markdown</div>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.txt,.md,text/plain,text/markdown,application/pdf"
          className="hidden"
          onChange={(e) => {
            handleFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <button
          className="btn btn-secondary mt-1"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          {busy ? "Uploading…" : "Browse files"}
        </button>
      </div>

      {kbError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {kbError}
        </div>
      )}

      {uploads.length > 0 && (
        <ul className="rounded-lg border border-slate-200 px-4">
          {uploads.map((item) => (
            <UploadRow key={item.fileName} item={item} onSettled={onSettled} />
          ))}
        </ul>
      )}

      {uploads.some((u) => u.state === "completed") && (
        <Callout tone="info">
          You can continue as soon as one document has finished. The rest keep
          processing in the background.
        </Callout>
      )}

      <Advanced title="Use an existing knowledge base instead">
        <select
          className="input"
          value={form.kbId ?? ""}
          onChange={(e) => patch({ kbId: e.target.value || null })}
        >
          <option value="">— Create a new one on upload —</option>
          {existingKbs.map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name} ({kb.document_count} docs, {kb.chunk_count} chunks)
            </option>
          ))}
        </select>
        <p className="text-xs text-slate-500">
          Selecting a knowledge base that already has ingested documents lets you
          continue without uploading anything new.
        </p>
      </Advanced>
    </div>
  );
}

function StepModel({ form, patch }: { form: Form; patch: (p: Partial<Form>) => void }) {
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const providers = useQuery({
    queryKey: ["providers", "llm"],
    queryFn: () => providersApi.list("llm"),
  });

  const selected = providers.data?.find((p) => p.id === form.llmProviderId);
  const models = useQuery({
    queryKey: ["provider-models", form.llmProviderId],
    queryFn: () => providersApi.models(form.llmProviderId),
    enabled: !!form.llmProviderId,
  });

  const choose = (p: Provider | undefined) => {
    if (!p) {
      patch({ llmProviderId: "", llmProvider: "ollama", llmEndpoint: "" });
      return;
    }
    patch({
      llmProviderId: p.id,
      llmProvider: p.provider_type,
      llmEndpoint: p.endpoint ?? "",
      llmModel: p.model || form.llmModel,
    });
    setTest(null);
  };

  const runTest = async () => {
    if (!form.llmProviderId) return;
    setTesting(true);
    try {
      setTest(await providersApi.test(form.llmProviderId));
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

  const ready = test?.connected === true;

  return (
    <div className="max-w-xl space-y-5">
      <div>
        <label className="label">Provider</label>
        <select
          className="input"
          value={form.llmProviderId}
          onChange={(e) => choose(providers.data?.find((p) => p.id === e.target.value))}
        >
          <option value="">— Not registered / use defaults —</option>
          {(providers.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.provider_type})
            </option>
          ))}
        </select>
        {providers.data?.length === 0 && (
          <p className="mt-2 text-xs text-slate-500">
            No LLM providers registered.{" "}
            <Link href="/providers" className="text-brand-600 hover:underline">
              Add one
            </Link>{" "}
            or type a model name below.
          </p>
        )}
      </div>

      <div>
        <label className="label">Model</label>
        {models.data?.reachable && models.data.models.length > 0 ? (
          <select
            className="input"
            value={form.llmModel}
            onChange={(e) => patch({ llmModel: e.target.value })}
          >
            {models.data.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="input"
            value={form.llmModel}
            onChange={(e) => patch({ llmModel: e.target.value })}
            placeholder="llama3.2:1b"
          />
        )}
        {form.llmProviderId && models.data && !models.data.reachable && (
          <p className="mt-2 text-xs text-amber-700">
            Could not list models from this provider — enter the name manually.
            {models.data.detail ? ` (${models.data.detail})` : ""}
          </p>
        )}
      </div>

      {selected && (
        <div className="flex items-center gap-3">
          <button className="btn btn-secondary" onClick={runTest} disabled={testing}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {test && (
            <span className={`text-xs ${ready ? "text-emerald-600" : "text-red-600"}`}>
              {test.message}
              {test.latency_ms != null && ` · ${test.latency_ms}ms`}
              {test.model_available === false && " · model not found on provider"}
            </span>
          )}
        </div>
      )}

      <Advanced>
        <div>
          <label className="label">System prompt</label>
          <textarea
            className="input"
            rows={3}
            value={form.systemPrompt}
            placeholder="You are a helpful assistant for Acme Corp employees."
            onChange={(e) => patch({ systemPrompt: e.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Retrieval mode</label>
            <select
              className="input"
              value={form.retrievalMode}
              onChange={(e) => patch({ retrievalMode: e.target.value as RetrievalMode })}
            >
              <option value="hybrid">Hybrid (BM25 + vector)</option>
              <option value="bm25">BM25 only</option>
              <option value="vector">Vector only</option>
            </select>
          </div>
          <div>
            <label className="label">Top K</label>
            <input
              className="input"
              type="number"
              min={1}
              max={100}
              value={form.topK}
              onChange={(e) => patch({ topK: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="label">Score threshold</label>
            <input
              className="input"
              type="number"
              step="0.01"
              value={form.scoreThreshold}
              placeholder="none"
              onChange={(e) => patch({ scoreThreshold: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Temperature</label>
            <input
              className="input"
              type="number"
              step="0.1"
              min={0}
              max={2}
              value={form.temperature}
              onChange={(e) => patch({ temperature: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="label">Max tokens</label>
            <input
              className="input"
              type="number"
              min={1}
              max={32768}
              value={form.maxTokens}
              onChange={(e) => patch({ maxTokens: Number(e.target.value) })}
            />
          </div>
        </div>
        <div className="flex gap-6">
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={form.citationsEnabled}
              onChange={(e) => patch({ citationsEnabled: e.target.checked })}
            />
            Return citations
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={form.streamingEnabled}
              onChange={(e) => patch({ streamingEnabled: e.target.checked })}
            />
            Allow streaming
          </label>
        </div>
      </Advanced>
    </div>
  );
}

function StepReview({
  form,
  slug,
  ingestedCount,
}: {
  form: Form;
  slug: string;
  ingestedCount: number;
}) {
  return (
    <div className="max-w-2xl space-y-5">
      <p className="text-base text-slate-700">
        <span className="font-semibold">{form.name}</span> will answer from{" "}
        <span className="font-semibold">
          {ingestedCount} document{ingestedCount === 1 ? "" : "s"}
        </span>{" "}
        using <span className="font-semibold">{form.llmModel}</span>, retrieving the
        top {form.topK} passages in {form.retrievalMode} mode.
      </p>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-lg bg-slate-50 p-4 text-sm">
        {[
          ["Model name (API)", slug],
          ["Provider", form.llmProvider],
          ["Citations", form.citationsEnabled ? "Enabled" : "Disabled"],
          ["Streaming", form.streamingEnabled ? "Enabled" : "Disabled"],
        ].map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4">
            <dt className="text-slate-500">{k}</dt>
            <dd className="font-medium text-slate-800">{v}</dd>
          </div>
        ))}
      </dl>

      <Callout tone="info" title="What happens next">
        We&apos;ll create the assistant and issue an API key. The key is shown once
        on the next screen — copy it before you leave.
      </Callout>
    </div>
  );
}
