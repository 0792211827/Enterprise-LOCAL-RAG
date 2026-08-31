"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  Callout,
  CodeBlock,
  CopyButton,
  SecretReveal,
  StatusBadge,
  Tabs,
  confirmDialog,
} from "@/components/ui";
import { API_BASE } from "@/lib/api/client";
import { applicationsApi, openaiApi, systemApi } from "@/lib/api/resources";
import { curlSnippet, pythonSnippet, typescriptSnippet } from "@/lib/snippets";
import type { Application, ChatCompletionResponse } from "@/lib/api/types";

interface Check {
  label: string;
  ok: boolean;
  remedy: string;
}

export function ReadinessStrip({ app, checks }: { app: Application; checks: Check[] }) {
  const ready = checks.every((c) => c.ok);
  return (
    <section className="card mb-6 p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">Readiness</h2>
        <StatusBadge status={ready ? "healthy" : "degraded"} />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {checks.map((c) => (
          <div
            key={c.label}
            className={`rounded-lg border p-3 ${
              c.ok ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"
            }`}
            title={c.ok ? undefined : c.remedy}
          >
            <div className="flex items-center gap-2">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${
                  c.ok ? "bg-emerald-500" : "bg-amber-500"
                }`}
              />
              <span
                className={`text-xs font-medium ${
                  c.ok ? "text-emerald-800" : "text-amber-900"
                }`}
              >
                {c.label}
              </span>
            </div>
            {!c.ok && <p className="mt-1 text-[11px] text-amber-800">{c.remedy}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}

export function EndpointPanel({ app, ready }: { app: Application; ready: boolean }) {
  const qc = useQueryClient();
  const [baseUrl, setBaseUrl] = useState(API_BASE);
  const [tab, setTab] = useState("curl");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // The wizard hands the one-time plaintext over in memory rather than
  // persisting it anywhere.
  useEffect(() => {
    const w = window as unknown as { __ragNewKey?: string };
    if (w.__ragNewKey) {
      setNewKey(w.__ragNewKey);
      delete w.__ragNewKey;
    }
  }, []);

  const keys = useQuery({
    queryKey: ["app-keys", app.id],
    queryFn: () => applicationsApi.keys(app.id),
  });
  const activeKeys = (keys.data ?? []).filter((k) => k.is_active);

  const createKey = useMutation({
    mutationFn: () => applicationsApi.createKey(app.id, "Manual key"),
    onSuccess: (k) => {
      setNewKey(k.key);
      qc.invalidateQueries({ queryKey: ["app-keys", app.id] });
    },
  });
  const rotateKey = useMutation({
    mutationFn: (keyId: string) => applicationsApi.rotateKey(app.id, keyId),
    onSuccess: (k) => {
      setNewKey(k.key);
      qc.invalidateQueries({ queryKey: ["app-keys", app.id] });
    },
  });
  const revokeKey = useMutation({
    mutationFn: (keyId: string) => applicationsApi.revokeKey(app.id, keyId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["app-keys", app.id] }),
  });

  const sendTest = useMutation({
    mutationFn: (): Promise<ChatCompletionResponse> =>
      openaiApi.chatCompletion(
        baseUrl,
        app.slug,
        [{ role: "user", content: "Give me a one sentence summary of what you know." }],
        newKey ?? undefined,
      ),
    onSuccess: (data) => {
      setTestError(null);
      setTestResult(JSON.stringify(data, null, 2));
    },
    onError: (e) => {
      setTestResult(null);
      setTestError((e as Error).message);
    },
  });

  const snippets: Record<string, string> = {
    curl: curlSnippet(baseUrl, app.slug, newKey),
    python: pythonSnippet(baseUrl, app.slug, newKey),
    typescript: typescriptSnippet(baseUrl, app.slug, newKey),
  };

  return (
    <section className="card mt-6 p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Endpoint</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            OpenAI-compatible. Point any OpenAI SDK at this URL.
          </p>
        </div>
        <StatusBadge status={ready ? "healthy" : "degraded"} />
      </div>

      {newKey && (
        <div className="mb-4">
          <Callout tone="warn" title="Your new API key — shown once">
            <div className="mt-2">
              <SecretReveal value={newKey} masked={`${newKey.slice(0, 11)}…${newKey.slice(-4)}`} />
            </div>
            <p className="mt-2 text-xs">
              Copy it now. It is stored hashed and cannot be shown again — create a
              new key if you lose it.
            </p>
          </Callout>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="label">Base URL</label>
          <div className="flex gap-2">
            <input
              className="input font-mono text-xs"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
            <CopyButton value={`${baseUrl}/v1`} />
          </div>
          <p className="mt-1 text-xs text-slate-400">
            As reachable from this browser. A server on another host or container
            needs that network&apos;s address — e.g. <code>http://api:8000</code>.
          </p>
        </div>
        <div>
          <label className="label">Model name</label>
          <div className="flex gap-2">
            <input className="input font-mono text-xs" value={app.slug} readOnly />
            <CopyButton value={app.slug} />
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Pass this as the <code>model</code> parameter.
          </p>
        </div>
      </div>

      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700">API keys</h3>
          <button
            className="btn btn-secondary"
            onClick={() => createKey.mutate()}
            disabled={createKey.isPending}
          >
            {createKey.isPending ? "Creating…" : "Create key"}
          </button>
        </div>

        <Callout tone="warn">
          Keys are issued but <strong>not yet enforced</strong>. The endpoint currently
          accepts any request, with or without an <code>Authorization</code> header.
          Treat it as unauthenticated and keep it on a trusted network.
        </Callout>

        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs uppercase text-slate-400">
              <th className="py-2 text-left font-medium">Key</th>
              <th className="py-2 text-left font-medium">Name</th>
              <th className="py-2 text-left font-medium">Created</th>
              <th className="py-2 text-left font-medium">Last used</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {activeKeys.map((k) => (
              <tr key={k.id} className="border-b border-slate-100 last:border-0">
                <td className="py-2">
                  <code className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700">
                    {k.key_prefix}…{k.key_last4}
                  </code>
                </td>
                <td className="py-2 text-slate-600">{k.name ?? "—"}</td>
                <td className="py-2 text-slate-500">
                  {k.created_at ? new Date(k.created_at).toLocaleString() : "—"}
                </td>
                <td className="py-2 text-slate-500">
                  {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}
                </td>
                <td className="py-2 text-right">
                  <button
                    className="mr-2 text-xs text-brand-600 hover:underline"
                    onClick={() => rotateKey.mutate(k.id)}
                  >
                    Rotate
                  </button>
                  <button
                    className="text-xs text-red-600 hover:underline"
                    onClick={() =>
                      confirmDialog("Revoke this key? Clients using it will stop working.") &&
                      revokeKey.mutate(k.id)
                    }
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
            {activeKeys.length === 0 && (
              <tr>
                <td colSpan={5} className="py-4 text-center text-sm text-slate-400">
                  No active keys.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-6">
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Usage</h3>
        <Tabs
          tabs={[
            { id: "curl", label: "curl" },
            { id: "python", label: "Python" },
            { id: "typescript", label: "TypeScript" },
          ]}
          value={tab}
          onChange={setTab}
        />
        <CodeBlock
          code={snippets[tab]}
          action={<CopyButton value={snippets[tab]} className="!px-2 !py-1 !text-xs" />}
        />
        <p className="mt-2 text-xs text-slate-400">
          Only the last <code>user</code> message is used — conversation history is
          not carried into retrieval yet.
        </p>
      </div>

      <div className="mt-6 border-t border-slate-100 pt-4">
        <div className="flex items-center gap-3">
          <button
            className="btn btn-primary"
            onClick={() => sendTest.mutate()}
            disabled={sendTest.isPending}
          >
            {sendTest.isPending ? "Sending…" : "Send test request"}
          </button>
          <span className="text-xs text-slate-400">
            Calls <code>POST {baseUrl}/v1/chat/completions</code> — the same route as
            the snippets above.
          </span>
        </div>
        {testError && (
          <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {testError}
          </div>
        )}
        {testResult && (
          <div className="mt-3">
            <CodeBlock code={testResult} />
          </div>
        )}
      </div>
    </section>
  );
}

export function useReadinessChecks(app: Application) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: systemApi.health,
    refetchInterval: 15000,
  });
  const keys = useQuery({
    queryKey: ["app-keys", app.id],
    queryFn: () => applicationsApi.keys(app.id),
  });

  const inference = health.data?.components.find((c) => c.name === "Inference");
  const hasContent = app.knowledge_bases.some((kb) => kb.chunk_count > 0);
  const model = app.model_configuration?.llm_model;

  const checks: Check[] = [
    {
      label: "Inference reachable",
      ok: inference?.status === "healthy",
      remedy: inference?.detail || "The inference provider is not responding. Check it is running.",
    },
    {
      label: "Model configured",
      ok: !!model,
      remedy: "No model is set on this assistant.",
    },
    {
      label: "Knowledge indexed",
      ok: hasContent,
      remedy:
        app.knowledge_bases.length === 0
          ? "No knowledge base attached — this assistant cannot answer anything."
          : "Attached knowledge bases have no indexed chunks yet. Upload a document.",
    },
    {
      label: "API key active",
      ok: (keys.data ?? []).some((k) => k.is_active),
      remedy: "No active key. Create one below.",
    },
  ];

  return checks;
}
