"use client";
import { useQuery } from "@tanstack/react-query";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { documentsApi } from "@/lib/api/resources";

function fmt(ts: string | null) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString();
}

export default function DocumentsPage() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: documentsApi.jobs,
    refetchInterval: 5_000,
  });

  return (
    <div>
      <PageHeader
        title="Ingestion Jobs"
        description="Live status of document parsing, chunking and embedding across all knowledge bases."
      />
      {jobs.isLoading && <LoadingState />}
      {jobs.isError && <ErrorState message={(jobs.error as Error).message} />}
      {jobs.data && jobs.data.length === 0 && (
        <EmptyState
          title="No ingestion jobs"
          description="Upload documents to a knowledge base to kick off ingestion."
        />
      )}
      {jobs.data && jobs.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3 font-medium">Job</th>
                <th className="px-4 py-3 font-medium">Stage</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Started</th>
                <th className="px-4 py-3 font-medium">Finished</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => (
                <tr key={j.id} className="border-t border-slate-100 align-top">
                  <td className="px-5 py-3">
                    <div className="font-mono text-xs text-slate-500">
                      {j.document_id.slice(0, 8)}
                    </div>
                    {j.error && <div className="text-xs text-red-500">{j.error}</div>}
                    {j.stats && (
                      <div className="text-xs text-slate-400">
                        {Object.entries(j.stats)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(" · ")}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">{j.stage ?? "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={j.status} />
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">{fmt(j.started_at)}</td>
                  <td className="px-4 py-3 text-xs text-slate-500">{fmt(j.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
