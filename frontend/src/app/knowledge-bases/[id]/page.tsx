"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MetricCard,
  PageHeader,
  StatusBadge,
  confirmDialog,
} from "@/components/ui";
import { documentsApi, knowledgeBasesApi } from "@/lib/api/resources";

export default function KnowledgeBaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  const kb = useQuery({ queryKey: ["kb", id], queryFn: () => knowledgeBasesApi.get(id) });
  const docs = useQuery({
    queryKey: ["docs", id],
    queryFn: () => documentsApi.listForKb(id),
    refetchInterval: 5_000,
  });

  const upload = useMutation({
    mutationFn: (file: File) => documentsApi.upload(id, file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docs", id] });
      qc.invalidateQueries({ queryKey: ["kb", id] });
    },
    onError: (e) => setError((e as Error).message),
  });

  const removeDoc = useMutation({
    mutationFn: documentsApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["docs", id] }),
  });
  const reprocess = useMutation({
    mutationFn: documentsApi.reprocess,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["docs", id] }),
  });

  if (kb.isLoading) return <LoadingState />;
  if (kb.isError) return <ErrorState message={(kb.error as Error).message} />;

  return (
    <div>
      <Link href="/knowledge-bases" className="text-sm text-brand-600">
        ← Knowledge Bases
      </Link>
      <PageHeader
        title={kb.data!.name}
        description={kb.data!.description || "No description"}
        action={
          <>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              onChange={(e) => {
                setError(null);
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
            <button
              className="btn btn-primary"
              disabled={upload.isPending}
              onClick={() => fileRef.current?.click()}
            >
              {upload.isPending ? "Uploading…" : "+ Upload Document"}
            </button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard label="Documents" value={kb.data!.document_count} />
        <MetricCard label="Chunks" value={kb.data!.chunk_count} />
        <MetricCard label="Retrieval" value={kb.data!.retrieval_mode} />
        <MetricCard label="Embedding Dim" value={kb.data!.embedding_dimension} />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="card mt-6">
        <div className="border-b border-slate-100 px-5 py-4 text-sm font-semibold text-slate-700">
          Documents
        </div>
        {docs.isLoading && <LoadingState />}
        {docs.data && docs.data.length === 0 && (
          <EmptyState
            title="No documents"
            description="Upload PDF, TXT or Markdown files to ingest them into this knowledge base."
          />
        )}
        {docs.data && docs.data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3 font-medium">Title</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Chunks</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {docs.data.map((d) => (
                <tr key={d.id} className="border-t border-slate-100">
                  <td className="px-5 py-3">
                    <div className="font-medium text-slate-700">{d.title}</div>
                    {d.error && <div className="text-xs text-red-500">{d.error}</div>}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {d.content_type ?? "—"}
                  </td>
                  <td className="px-4 py-3">{d.chunk_count}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={d.status} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button
                        className="btn btn-secondary"
                        onClick={() => reprocess.mutate(d.id)}
                      >
                        Reprocess
                      </button>
                      <button
                        className="btn btn-danger"
                        onClick={() => {
                          if (confirmDialog(`Delete "${d.title}"?`)) removeDoc.mutate(d.id);
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
