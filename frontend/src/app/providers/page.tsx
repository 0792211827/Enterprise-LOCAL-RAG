"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  StatusBadge,
  confirmDialog,
} from "@/components/ui";
import { providersApi } from "@/lib/api/resources";
import type { ProviderCreate, ProviderTestResult } from "@/lib/api/types";

export default function ProvidersPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<Record<string, ProviderTestResult>>({});
  const list = useQuery({ queryKey: ["providers"], queryFn: () => providersApi.list() });

  const remove = useMutation({
    mutationFn: providersApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });
  const test = useMutation({
    mutationFn: providersApi.test,
    onSuccess: (data, id) => setResults((r) => ({ ...r, [id]: data })),
  });

  return (
    <div>
      <PageHeader
        title="Models & Providers"
        description="Register self-hosted LLM, embedding and vision providers."
        action={
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            + Add Provider
          </button>
        }
      />
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState message={(list.error as Error).message} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No providers configured"
          description="Add an Ollama, OpenAI-compatible or HuggingFace provider to power retrieval and generation."
        />
      )}
      {list.data && list.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Kind</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {list.data.map((p) => {
                const res = results[p.id];
                return (
                  <tr key={p.id} className="border-t border-slate-100 align-top">
                    <td className="px-5 py-3">
                      <div className="font-medium text-slate-700">{p.name}</div>
                      {p.endpoint && (
                        <div className="text-xs text-slate-400">{p.endpoint}</div>
                      )}
                      {res && (
                        <div
                          className={`mt-1 text-xs ${
                            res.connected ? "text-emerald-600" : "text-red-600"
                          }`}
                        >
                          {res.message}
                          {res.latency_ms != null && ` · ${res.latency_ms}ms`}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">{p.kind}</td>
                    <td className="px-4 py-3">{p.provider_type}</td>
                    <td className="px-4 py-3">{p.model}</td>
                    <td className="px-4 py-3">
                      <StatusBadge status={p.enabled ? "ok" : "disabled"} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-2">
                        <button
                          className="btn btn-secondary"
                          disabled={test.isPending}
                          onClick={() => test.mutate(p.id)}
                        >
                          Test
                        </button>
                        <button
                          className="btn btn-danger"
                          onClick={() => {
                            if (confirmDialog(`Delete "${p.name}"?`)) remove.mutate(p.id);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <CreateProviderModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function CreateProviderModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ProviderCreate>({
    name: "",
    kind: "llm",
    provider_type: "ollama",
    endpoint: "http://ollama:11434",
    model: "",
    enabled: true,
  });
  const create = useMutation({
    mutationFn: providersApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      onClose();
    },
  });

  return (
    <Modal open={open} onClose={onClose} title="Add Provider">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(form);
        }}
      >
        <div>
          <label className="label">Name</label>
          <input
            className="input"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Kind</label>
            <select
              className="input"
              value={form.kind}
              onChange={(e) =>
                setForm({ ...form, kind: e.target.value as ProviderCreate["kind"] })
              }
            >
              <option value="llm">LLM</option>
              <option value="embedding">Embedding</option>
              <option value="vlm">Vision (VLM)</option>
            </select>
          </div>
          <div>
            <label className="label">Type</label>
            <select
              className="input"
              value={form.provider_type}
              onChange={(e) =>
                setForm({
                  ...form,
                  provider_type: e.target.value as ProviderCreate["provider_type"],
                })
              }
            >
              <option value="ollama">Ollama</option>
              <option value="openai-compatible">OpenAI-compatible</option>
              <option value="huggingface">HuggingFace</option>
            </select>
          </div>
        </div>
        <div>
          <label className="label">Endpoint</label>
          <input
            className="input"
            value={form.endpoint ?? ""}
            onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Model</label>
          <input
            className="input"
            required
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="llama3.2:1b"
          />
        </div>
        {create.isError && (
          <p className="text-sm text-red-600">{(create.error as Error).message}</p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {create.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
