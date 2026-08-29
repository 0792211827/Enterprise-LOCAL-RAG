"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
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
import { applicationsApi, knowledgeBasesApi } from "@/lib/api/resources";
import type { ApplicationCreate } from "@/lib/api/types";

export default function ApplicationsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const list = useQuery({ queryKey: ["apps"], queryFn: applicationsApi.list });
  const remove = useMutation({
    mutationFn: applicationsApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["apps"] }),
  });

  return (
    <div>
      <PageHeader
        title="RAG Applications"
        description="Bind knowledge bases to a model and retrieval configuration."
        action={
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            + Create Application
          </button>
        }
      />
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState message={(list.error as Error).message} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No applications yet"
          description="Create a RAG application to expose your knowledge bases through a chat interface and API."
          action={
            <button className="btn btn-primary" onClick={() => setOpen(true)}>
              Create Application
            </button>
          }
        />
      )}
      {list.data && list.data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {list.data.map((app) => (
            <div key={app.id} className="card p-5">
              <div className="flex items-start justify-between">
                <div>
                  <Link
                    href={`/applications/${app.id}`}
                    className="text-lg font-semibold text-brand-700 hover:underline"
                  >
                    {app.name}
                  </Link>
                  <p className="mt-1 text-sm text-slate-500">
                    {app.description || "No description"}
                  </p>
                </div>
                <StatusBadge status={app.rag_strategy} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-500">
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.model_configuration?.llm_model ?? "no model"}
                </span>
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.retrieval_configuration?.mode ?? "hybrid"} · top-k{" "}
                  {app.retrieval_configuration?.top_k ?? "?"}
                </span>
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.knowledge_bases.length} KB
                </span>
              </div>
              <div className="mt-4 flex gap-2">
                <Link href={`/applications/${app.id}`} className="btn btn-secondary">
                  Open Playground
                </Link>
                <button
                  className="btn btn-danger"
                  onClick={() => {
                    if (confirmDialog(`Delete "${app.name}"?`)) remove.mutate(app.id);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <CreateAppModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function CreateAppModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list, enabled: open });
  const [form, setForm] = useState<ApplicationCreate>({
    name: "",
    llm_model: "llama3.2:1b",
    retrieval_mode: "hybrid",
    top_k: 8,
    rag_strategy: "traditional",
    knowledge_base_ids: [],
  });
  const create = useMutation({
    mutationFn: applicationsApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["apps"] });
      onClose();
    },
  });

  const toggleKb = (id: string) =>
    setForm((f) => {
      const set = new Set(f.knowledge_base_ids);
      set.has(id) ? set.delete(id) : set.add(id);
      return { ...f, knowledge_base_ids: [...set] };
    });

  return (
    <Modal open={open} onClose={onClose} title="Create RAG Application">
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
            placeholder="HR Assistant"
          />
        </div>
        <div>
          <label className="label">Knowledge Bases</label>
          <div className="max-h-32 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
            {kbs.data?.length ? (
              kbs.data.map((kb) => (
                <label key={kb.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.knowledge_base_ids?.includes(kb.id)}
                    onChange={() => toggleKb(kb.id)}
                  />
                  {kb.name}
                </label>
              ))
            ) : (
              <p className="text-xs text-slate-400">No knowledge bases available.</p>
            )}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label">LLM Model</label>
            <input
              className="input"
              value={form.llm_model}
              onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Retrieval</label>
            <select
              className="input"
              value={form.retrieval_mode}
              onChange={(e) =>
                setForm({ ...form, retrieval_mode: e.target.value as ApplicationCreate["retrieval_mode"] })
              }
            >
              <option value="hybrid">Hybrid</option>
              <option value="bm25">BM25</option>
              <option value="vector">Vector</option>
            </select>
          </div>
          <div>
            <label className="label">Top K</label>
            <input
              type="number"
              className="input"
              value={form.top_k}
              onChange={(e) => setForm({ ...form, top_k: Number(e.target.value) })}
            />
          </div>
        </div>
        <div>
          <label className="label">System Prompt</label>
          <textarea
            className="input"
            rows={2}
            value={form.system_prompt ?? ""}
            onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
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
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
