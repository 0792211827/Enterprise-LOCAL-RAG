"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  confirmDialog,
} from "@/components/ui";
import { retrievalApi } from "@/lib/api/resources";
import type { RetrievalConfig, RetrievalMode } from "@/lib/api/types";

export default function RetrievalPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const list = useQuery({ queryKey: ["retrieval"], queryFn: retrievalApi.list });
  const remove = useMutation({
    mutationFn: retrievalApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["retrieval"] }),
  });

  return (
    <div>
      <PageHeader
        title="Retrieval Configurations"
        description="Reusable BM25 / vector / hybrid search profiles with RRF tuning."
        action={
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            + New Configuration
          </button>
        }
      />
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState message={(list.error as Error).message} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No retrieval configurations"
          description="Create a profile to reuse consistent retrieval settings across applications."
        />
      )}
      {list.data && list.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Mode</th>
                <th className="px-4 py-3 font-medium">Top K</th>
                <th className="px-4 py-3 font-medium">Hybrid ×</th>
                <th className="px-4 py-3 font-medium">RRF k</th>
                <th className="px-4 py-3 font-medium">Score ≥</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {list.data.map((c) => (
                <tr key={c.id} className="border-t border-slate-100">
                  <td className="px-5 py-3 font-medium text-slate-700">{c.name}</td>
                  <td className="px-4 py-3">{c.mode}</td>
                  <td className="px-4 py-3">{c.top_k}</td>
                  <td className="px-4 py-3">{c.hybrid_size_multiplier}</td>
                  <td className="px-4 py-3">{c.rrf_rank_constant}</td>
                  <td className="px-4 py-3">{c.score_threshold ?? "—"}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      className="btn btn-danger"
                      onClick={() => {
                        if (confirmDialog(`Delete "${c.name}"?`)) remove.mutate(c.id);
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
      <CreateConfigModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

const EMPTY_CONFIG: Partial<RetrievalConfig> & { name: string } = {
  name: "",
  mode: "hybrid",
  top_k: 8,
  hybrid_size_multiplier: 2,
  rrf_rank_constant: 60,
};

function CreateConfigModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Partial<RetrievalConfig> & { name: string }>(EMPTY_CONFIG);
  const create = useMutation({
    mutationFn: retrievalApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["retrieval"] });
      setForm(EMPTY_CONFIG);
      onClose();
    },
  });

  return (
    <Modal open={open} onClose={onClose} title="New Retrieval Configuration">
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
            <label className="label">Mode</label>
            <select
              className="input"
              value={form.mode}
              onChange={(e) => setForm({ ...form, mode: e.target.value as RetrievalMode })}
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
          <div>
            <label className="label">Hybrid Multiplier</label>
            <input
              type="number"
              className="input"
              value={form.hybrid_size_multiplier}
              onChange={(e) =>
                setForm({ ...form, hybrid_size_multiplier: Number(e.target.value) })
              }
            />
          </div>
          <div>
            <label className="label">RRF Rank Constant</label>
            <input
              type="number"
              className="input"
              value={form.rrf_rank_constant}
              onChange={(e) =>
                setForm({ ...form, rrf_rank_constant: Number(e.target.value) })
              }
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
            {create.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
