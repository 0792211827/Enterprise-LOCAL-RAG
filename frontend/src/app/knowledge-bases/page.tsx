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
import { knowledgeBasesApi } from "@/lib/api/resources";
import type { KnowledgeBaseCreate } from "@/lib/api/types";

export default function KnowledgeBasesPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const list = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });

  const remove = useMutation({
    mutationFn: knowledgeBasesApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kbs"] }),
  });

  return (
    <div>
      <PageHeader
        title="Knowledge Bases"
        description="Collections of enterprise documents with isolated indexes."
        action={
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            + Create Knowledge Base
          </button>
        }
      />

      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState message={(list.error as Error).message} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No knowledge bases yet"
          description="Create your first knowledge base to start ingesting documents."
          action={
            <button className="btn btn-primary" onClick={() => setOpen(true)}>
              Create Knowledge Base
            </button>
          }
        />
      )}

      {list.data && list.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Documents</th>
                <th className="px-4 py-3 font-medium">Chunks</th>
                <th className="px-4 py-3 font-medium">Retrieval</th>
                <th className="px-4 py-3 font-medium">Embedding</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {list.data.map((kb) => (
                <tr key={kb.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/knowledge-bases/${kb.id}`}
                      className="font-medium text-brand-700 hover:underline"
                    >
                      {kb.name}
                    </Link>
                    <div className="text-xs text-slate-400">{kb.slug}</div>
                  </td>
                  <td className="px-4 py-3">{kb.document_count}</td>
                  <td className="px-4 py-3">{kb.chunk_count}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={kb.retrieval_mode} />
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">{kb.embedding_model}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      className="btn btn-danger"
                      onClick={() => {
                        if (confirmDialog(`Delete "${kb.name}"? This removes its documents.`))
                          remove.mutate(kb.id);
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreateKbModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function CreateKbModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<KnowledgeBaseCreate>({
    name: "",
    description: "",
    retrieval_mode: "hybrid",
    default_top_k: 8,
  });
  const create = useMutation({
    mutationFn: knowledgeBasesApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setForm({ name: "", description: "", retrieval_mode: "hybrid", default_top_k: 8 });
      onClose();
    },
  });

  return (
    <Modal open={open} onClose={onClose} title="Create Knowledge Base">
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
            placeholder="HR Policies"
          />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea
            className="input"
            rows={2}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Retrieval Mode</label>
            <select
              className="input"
              value={form.retrieval_mode}
              onChange={(e) =>
                setForm({ ...form, retrieval_mode: e.target.value as KnowledgeBaseCreate["retrieval_mode"] })
              }
            >
              <option value="hybrid">Hybrid (BM25 + Vector)</option>
              <option value="bm25">BM25</option>
              <option value="vector">Vector</option>
            </select>
          </div>
          <div>
            <label className="label">Default Top K</label>
            <input
              type="number"
              min={1}
              max={100}
              className="input"
              value={form.default_top_k}
              onChange={(e) => setForm({ ...form, default_top_k: Number(e.target.value) })}
            />
          </div>
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
